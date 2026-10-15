"""One pass of the deal scanner: fetch what changed on Grailed, store it, judge it.

For every brand in scope the scanner asks the active index for listings created or repriced
since it last looked (``deal_scans.scanned_to``, never more than a day back), upserts them
without a parser run, regroups the touched brands incrementally and judges the listings it
just saw. The network is behind ``Search`` so the pass is testable offline.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.db.models import Brand, DealScan, GroupMetric, ModelGroup, SourceCredential, WatchlistItem
from app.repositories.listings import ListingRepository
from app.services.deals.candidates import DealReport, find_deals, rules_from
from app.services.metrics.fees import load_fees
from app.services.normalization.mapping import load_source_mapping
from app.services.normalization.normalizer import ListingNormalizer, NormalizationContext
from app.services.normalization.quality import QualityProcessor
from app.services.parser.planner import collection_filters, verified_mappings
from app.services.per_brand import regroup_brands
from app.services.sources.base.models import RawHit
from app.services.sources.grailed.algolia.models import AlgoliaPage, AlgoliaQuery

# A brand seen for the first time, or after a long pause, is scanned this far back.
LOOKBACK = timedelta(hours=24)
# Re-read a little before the last scan: Algolia indexes a listing a moment after it is made.
OVERLAP = timedelta(minutes=5)
MAX_PAGES = 5
ERROR_LENGTH = 500

Search = Callable[[str, AlgoliaQuery], Awaitable[AlgoliaPage]]


@dataclass(frozen=True, slots=True)
class Target:
    brand_id: int
    name: str
    facets: tuple[str, ...]
    since: datetime


@dataclass(frozen=True, slots=True)
class ScanReport:
    started_at: datetime
    finished_at: datetime
    brands: int
    hits: int
    stored: int
    failed: dict[str, str] = field(default_factory=dict)
    truncated: tuple[str, ...] = ()
    deals: DealReport | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "brands": self.brands,
            "hits": self.hits,
            "stored": self.stored,
            "failed": dict(self.failed),
            "truncated": list(self.truncated),
            "deals": self.deals.as_dict() if self.deals else None,
        }


async def brands_in_scope(session: AsyncSession, settings: Settings) -> list[int]:
    """Watched models' brands, or every brand with a model liquid enough to hold a deal."""

    if settings.deals_scope == "watchlist":
        statement = select(ModelGroup.brand_id).join(
            WatchlistItem, WatchlistItem.group_id == ModelGroup.id
        )
    else:
        statement = select(GroupMetric.brand_id).where(
            GroupMetric.scope == "model",
            GroupMetric.liquidity_score >= settings.deals_min_liquidity_score,
        )
    rows = await session.scalars(statement.distinct())
    return sorted(brand_id for brand_id in rows if brand_id is not None)


async def targets(
    session: AsyncSession, credential: SourceCredential, brand_ids: Sequence[int], now: datetime
) -> list[Target]:
    brands = await session.scalars(
        select(Brand)
        .where(Brand.id.in_(brand_ids))
        .options(selectinload(Brand.source_mappings))
        .order_by(Brand.id)
    )
    scanned = {
        scan.brand_id: _aware(scan.scanned_to)
        for scan in await session.scalars(select(DealScan).where(DealScan.brand_id.in_(brand_ids)))
    }
    facet = credential.brand_facet or "designers.name"
    found = []
    for brand in brands:
        mappings = verified_mappings(brand.source_mappings, brand.include_subbrands)
        if not mappings:
            continue
        last = scanned.get(brand.id)
        since = max(last - OVERLAP, now - LOOKBACK) if last else now - LOOKBACK
        names = tuple(f"{facet}:{item.source_designer_name}" for item in mappings)
        found.append(Target(brand.id, brand.name, names, since))
    return found


async def fetch(
    search: Search, settings: Settings, credential: SourceCredential, target: Target
) -> tuple[list[dict[str, Any]], bool]:
    """The target's changed active hits, and whether there were more than a scan reads."""

    assert credential.active_index is not None
    stamp = int(target.since.timestamp())
    per_page = min(settings.algolia_hits_per_page, credential.max_hits_per_page or 1_000)
    readable = credential.pagination_limit or 1_000
    hits: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        if page * per_page >= readable:
            break
        result = await search(
            credential.active_index,
            AlgoliaQuery(
                hits_per_page=per_page,
                page=page,
                filters=f"created_at_i > {stamp} OR price_updated_at_i > {stamp}",
                facet_filters=(target.facets,),
                numeric_filters=collection_filters(settings, "active"),
            ),
        )
        hits.extend(result.hits)
        if not result.hits or page + 1 >= result.nb_pages:
            return hits, False
    return hits, True


async def store(
    session: AsyncSession, settings: Settings, brand_id: int, hits: Sequence[dict[str, Any]]
) -> int:
    """Upsert the hits as active listings of no parser run; the caller commits."""

    normalizer = ListingNormalizer(load_source_mapping(), settings=settings)
    context = NormalizationContext(
        status="active",
        parser_run_id=None,
        observed_at=datetime.now(UTC),
        brand_id=brand_id,
        fetch_tier="T1",
    )
    listings = []
    for hit in hits:
        result = await normalizer.normalize(RawHit(dict(hit), "T1"), context)
        if result.listing is not None:
            listings.append(result.listing)
    if not listings:
        return 0
    upsert = await ListingRepository(session).upsert_batch(
        QualityProcessor(settings).apply(listings)
    )
    return upsert.inserted + upsert.updated


async def run_scan(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    search: Search,
    credential: SourceCredential,
    *,
    grouping_lock: asyncio.Lock,
    now: datetime | None = None,
) -> ScanReport:
    started = now or datetime.now(UTC)
    async with sessions() as session:
        brand_ids = await brands_in_scope(session, settings)
        scope = await targets(session, credential, brand_ids, started)
    hits = stored = 0
    failed: dict[str, str] = {}
    truncated: list[str] = []
    touched: list[int] = []
    for target in scope:
        try:
            found, more = await fetch(search, settings, credential, target)
            async with sessions() as session:
                saved = await store(session, settings, target.brand_id, found)
                await _mark(session, target.brand_id, started, len(found), None)
                await session.commit()
        except Exception as exc:
            structlog.get_logger(__name__).warning(
                "deal_scan_brand_failed", brand_id=target.brand_id, error_type=type(exc).__name__
            )
            failed[target.name] = f"{type(exc).__name__}: {exc}"[:ERROR_LENGTH]
            async with sessions() as session:
                await _mark(session, target.brand_id, None, 0, failed[target.name])
                await session.commit()
            continue
        hits += len(found)
        stored += saved
        if more:
            truncated.append(target.name)
        if saved:
            touched.append(target.brand_id)
    deals = await _judge(sessions, settings, grouping_lock, touched, started) if touched else None
    return ScanReport(
        started_at=started,
        finished_at=datetime.now(UTC),
        brands=len(scope),
        hits=hits,
        stored=stored,
        failed=failed,
        truncated=tuple(truncated),
        deals=deals,
    )


async def _judge(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    grouping_lock: asyncio.Lock,
    brand_ids: Sequence[int],
    since: datetime,
) -> DealReport:
    async with sessions() as session:
        # A listing is judged by its model, so new listings need one first.
        async with grouping_lock:
            await regroup_brands(session, brand_ids)
        report = await find_deals(
            session, rules_from(settings), load_fees(), since=since, by="seen", brand_ids=brand_ids
        )
        await session.commit()
    return report


async def _mark(
    session: AsyncSession,
    brand_id: int,
    scanned_to: datetime | None,
    hits: int,
    error: str | None,
) -> None:
    """Record the brand's scan; a failed one keeps where the last good scan stopped."""

    row = await session.get(DealScan, brand_id)
    if row is None:
        session.add(
            DealScan(
                brand_id=brand_id,
                scanned_to=scanned_to or datetime.now(UTC) - LOOKBACK,
                hits=hits,
                error=error,
            )
        )
        return
    if scanned_to is not None:
        row.scanned_to = scanned_to
    row.hits = hits
    row.error = error


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
