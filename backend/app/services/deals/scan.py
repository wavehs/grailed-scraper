"""One pass of the deal scanner: fetch what changed on Grailed, store it, judge it.

For every brand in scope the scanner asks the active index for listings created or repriced
since it last looked (``deal_scans.scanned_to``, never more than a day back), upserts them
without a parser run, regroups the touched brands incrementally and judges the listings it
just saw. A brand's cursor moves only after its hits are judged, so a failure re-reads the
window next time. A window holding more than Algolia lets a query read is halved until each
part fits. The network is behind ``Search`` so the pass is testable offline.
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
# Halving stops at this width or after this many requests for one brand; the rest is truncated.
MIN_SLICE_S = 10 * 60
MAX_REQUESTS = 30
# This many brands failing in a row (a throttle, a challenge) ends the pass.
FAILURE_STREAK = 3
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
    aborted: bool = False

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
            "aborted": self.aborted,
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
    search: Search,
    settings: Settings,
    credential: SourceCredential,
    target: Target,
    until: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    """The target's hits created or repriced in (since, until], and whether some were left."""

    windows = [(int(target.since.timestamp()), int(until.timestamp()))]
    found: dict[str, dict[str, Any]] = {}
    truncated = False
    requests = 0
    while windows:
        start, end = windows.pop()
        hits, more, used = await _window(search, settings, credential, target.facets, start, end)
        requests += used
        if more and end - start > MIN_SLICE_S and requests < MAX_REQUESTS:
            middle = (start + end) // 2
            windows.extend(((middle, end), (start, middle)))
            continue
        truncated = truncated or more
        # A listing made in one half and repriced in the other comes back twice.
        for hit in hits:
            found.setdefault(str(hit.get("objectID") or hit.get("id") or id(hit)), hit)
    return list(found.values()), truncated


async def _window(
    search: Search,
    settings: Settings,
    credential: SourceCredential,
    facets: tuple[str, ...],
    start: int,
    end: int,
) -> tuple[list[dict[str, Any]], bool, int]:
    """One window's hits, whether it holds more than a query reads, and the requests made."""

    assert credential.active_index is not None
    per_page = min(settings.algolia_hits_per_page, credential.max_hits_per_page or 1_000)
    readable = credential.pagination_limit or 1_000
    span = f"{start + 1} TO {end}"
    hits: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        if page * per_page >= readable:
            return hits, True, page
        result = await search(
            credential.active_index,
            AlgoliaQuery(
                hits_per_page=per_page,
                page=page,
                filters=f"created_at_i:{span} OR price_updated_at_i:{span}",
                facet_filters=(facets,),
                numeric_filters=collection_filters(settings, "active"),
            ),
        )
        hits.extend(result.hits)
        if result.nb_hits > readable:
            return hits, True, page + 1
        if not result.hits or page + 1 >= result.nb_pages:
            return hits, False, page + 1
    return hits, True, MAX_PAGES


async def store(
    session: AsyncSession, settings: Settings, brand_id: int, hits: Sequence[dict[str, Any]]
) -> tuple[int, int]:
    """Upsert the hits as active listings of no parser run: (inserted, updated); the caller
    commits."""

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
        return 0, 0
    upsert = await ListingRepository(session).upsert_batch(
        QualityProcessor(settings).apply(listings)
    )
    return upsert.inserted, upsert.updated


@dataclass(slots=True)
class _Pass:
    hits: int = 0
    stored: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    truncated: list[str] = field(default_factory=list)
    # brand id -> hits, for the cursors moved once the hits are judged
    done: dict[int, int] = field(default_factory=dict)
    touched: list[int] = field(default_factory=list)
    inserted: list[int] = field(default_factory=list)
    aborted: bool = False


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
    state = await _collect(sessions, settings, search, credential, scope, started)
    deals = None
    names = {target.brand_id: target.name for target in scope}
    if state.touched:
        try:
            deals = await _judge(sessions, settings, grouping_lock, state, started)
        except Exception as exc:
            _log_failure("deal_scan_judge_failed", None, exc)
            error = f"judge: {type(exc).__name__}: {exc}"[:ERROR_LENGTH]
            for brand_id in state.touched:
                state.failed[names[brand_id]] = error
                await _record(sessions, brand_id, None, state.done.pop(brand_id), error)
    for brand_id, hits in state.done.items():
        await _record(sessions, brand_id, started, hits, None)
    return ScanReport(
        started_at=started,
        finished_at=datetime.now(UTC),
        brands=len(scope),
        hits=state.hits,
        stored=state.stored,
        failed=state.failed,
        truncated=tuple(state.truncated),
        deals=deals,
        aborted=state.aborted,
    )


async def _collect(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    search: Search,
    credential: SourceCredential,
    scope: Sequence[Target],
    started: datetime,
) -> _Pass:
    """Fetch and store every target's hits, one commit per brand."""

    state = _Pass()
    streak = 0
    for target in scope:
        try:
            found, more = await fetch(search, settings, credential, target, started)
            async with sessions() as session:
                inserted, updated = await store(session, settings, target.brand_id, found)
                await session.commit()
        except Exception as exc:
            _log_failure("deal_scan_brand_failed", target.brand_id, exc)
            state.failed[target.name] = f"{type(exc).__name__}: {exc}"[:ERROR_LENGTH]
            await _record(sessions, target.brand_id, None, 0, state.failed[target.name])
            streak += 1
            if streak >= FAILURE_STREAK:
                state.aborted = True
                break
            continue
        streak = 0
        state.hits += len(found)
        state.stored += inserted + updated
        state.done[target.brand_id] = len(found)
        if more:
            state.truncated.append(target.name)
        if inserted + updated:
            state.touched.append(target.brand_id)
        if inserted:
            state.inserted.append(target.brand_id)
    return state


async def _judge(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    grouping_lock: asyncio.Lock,
    state: _Pass,
    since: datetime,
) -> DealReport:
    async with sessions() as session:
        # A new listing is judged by its model, so it needs one first. A reprice keeps the
        # listing's grouping input, so brands with only updates are not regrouped; the lock is
        # taken per brand so that an edit on the Groups page waits for one brand at most.
        for brand_id in state.inserted:
            async with grouping_lock:
                await regroup_brands(session, [brand_id])
        report = await find_deals(
            session,
            rules_from(settings),
            load_fees(),
            since=since,
            by="seen",
            brand_ids=state.touched,
        )
        await session.commit()
    return report


async def _record(
    sessions: async_sessionmaker[AsyncSession],
    brand_id: int,
    scanned_to: datetime | None,
    hits: int,
    error: str | None,
) -> None:
    """Write the brand's scan state; a failure here is logged, not raised, so the pass goes on."""

    try:
        async with sessions() as session:
            await _mark(session, brand_id, scanned_to, hits, error)
            await session.commit()
    except Exception as exc:
        _log_failure("deal_scan_mark_failed", brand_id, exc)


def _log_failure(event: str, brand_id: int | None, exc: Exception) -> None:
    structlog.get_logger(__name__).warning(
        event, brand_id=brand_id, error_type=type(exc).__name__, error=str(exc)[:ERROR_LENGTH]
    )


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
