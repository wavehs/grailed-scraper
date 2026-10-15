"""Judge active listings from the database and keep the deals in ``deal_candidates``.

One row per listing: a deal found again is updated (``found_at`` and the person's verdict
stay); a stored deal that no longer qualifies is dropped unless someone already rated it,
since the ratings are the acceptance sample of v2 plan 4.1.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import String, cast, delete, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import DealCandidate, GroupMetric, Listing, ListingModelAssignment, ModelGroup
from app.services.deals.rules import DealRules, GroupFacts, Judgement, Offer, judge
from app.services.metrics.fees import FeeSchedule
from app.services.metrics.supply import SellerRow, dormant_wholesalers

# "seen": listings a scan just fetched; "changed": created or repriced (offline re-check).
WindowField = Literal["seen", "changed"]
_CHUNK = 500


@dataclass(frozen=True, slots=True)
class DealReport:
    evaluated: int
    deals: int
    new: int
    dropped: int
    reasons: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluated": self.evaluated,
            "deals": self.deals,
            "new": self.new,
            "dropped": self.dropped,
            "reasons": dict(sorted(self.reasons.items(), key=lambda item: -item[1])),
        }


@dataclass(frozen=True, slots=True)
class _Row:
    listing_id: int
    brand_id: int | None
    price: Decimal
    shipping: Decimal | None
    condition: str | None
    flags: list[str] | None
    seller: str | None
    rating_avg: Decimal | None
    rating_count: int | None
    trusted: bool | None
    group_id: int
    slug: str
    product_type: str
    status: str
    source: str
    support: int | None
    retired_at: datetime | None
    is_fallback: bool | None
    liquidity_score: Decimal | None
    pricing: dict[str, Any] | None


def rules_from(settings: Settings) -> DealRules:
    return DealRules(
        min_margin=settings.deals_min_margin,
        min_bucket_sales=settings.deals_min_bucket_sales,
        min_liquidity_score=settings.deals_min_liquidity_score,
    )


async def find_deals(
    session: AsyncSession,
    rules: DealRules,
    schedule: FeeSchedule,
    *,
    since: datetime,
    by: WindowField,
    brand_ids: Sequence[int] | None = None,
    now: datetime | None = None,
) -> DealReport:
    """Judge the active, grouped listings of the window; the caller commits."""

    moment = now or datetime.now(UTC)
    rows = await _rows(session, since, by, brand_ids)
    wholesale = await _wholesalers(session, rows, moment)
    judged = [
        (
            row,
            judge(
                _offer(row, (row.brand_id, row.seller) in wholesale), _group(row), rules, schedule
            ),
        )
        for row in rows
    ]
    deals = [(row, judgement) for row, judgement in judged if judgement.is_deal]
    new = await _store(session, deals, moment)
    dropped = await _drop(
        session, [row.listing_id for row, judgement in judged if not judgement.is_deal]
    )
    return DealReport(
        evaluated=len(judged),
        deals=len(deals),
        new=new,
        dropped=dropped,
        reasons=dict(Counter(reason for _, judgement in judged for reason in judgement.reasons)),
    )


async def _rows(
    session: AsyncSession, since: datetime, by: WindowField, brand_ids: Sequence[int] | None
) -> list[_Row]:
    window = (
        Listing.last_seen_at >= since
        if by == "seen"
        else or_(Listing.created_at >= since, Listing.price_updated_at >= since)
    )
    statement = (
        select(
            Listing.id,
            Listing.brand_id,
            Listing.price,
            Listing.ship_domestic,
            Listing.condition,
            Listing.quality_flags,
            Listing.seller_identity,
            Listing.seller_rating_avg,
            Listing.seller_rating_count,
            Listing.seller_trusted,
            ModelGroup.id,
            ModelGroup.slug,
            ModelGroup.product_type,
            ModelGroup.status,
            ModelGroup.source,
            ModelGroup.support,
            ModelGroup.retired_at,
            GroupMetric.is_fallback,
            GroupMetric.liquidity_score,
            GroupMetric.pricing,
        )
        .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
        .join(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
        # By the unique scope_key: (scope, group_id) has no index and scans every model.
        .outerjoin(GroupMetric, GroupMetric.scope_key == "model:" + cast(ModelGroup.id, String))
        .where(Listing.status == "active", window)
    )
    if brand_ids is not None:
        statement = statement.where(Listing.brand_id.in_(brand_ids))
    return [_Row(*row) for row in (await session.execute(statement)).tuples().all()]


async def _wholesalers(
    session: AsyncSession, rows: Sequence[_Row], now: datetime
) -> set[tuple[int | None, str | None]]:
    """(brand, seller) pairs of dormant wholesalers among the rows' sellers."""

    sellers: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        if row.brand_id is not None and row.seller is not None:
            sellers[row.brand_id].add(row.seller)
    found: set[tuple[int | None, str | None]] = set()
    for brand_id, names in sellers.items():
        ordered = sorted(names)
        stock: list[SellerRow] = []
        for start in range(0, len(ordered), _CHUNK):
            result = await session.execute(
                select(Listing.seller_identity, Listing.status, Listing.sold_at).where(
                    Listing.brand_id == brand_id,
                    Listing.seller_identity.in_(ordered[start : start + _CHUNK]),
                    Listing.status.in_(("active", "sold")),
                )
            )
            stock.extend(
                (seller, status, _aware(sold_at) if sold_at else None)
                for seller, status, sold_at in result.tuples()
            )
        found.update((brand_id, seller) for seller in dormant_wholesalers(stock, now))
    return found


def _offer(row: _Row, wholesale: bool) -> Offer:
    return Offer(
        price=row.price,
        shipping=row.shipping,
        condition=row.condition,
        quality_flags=tuple(row.flags or ()),
        seller_rating_avg=row.rating_avg,
        seller_rating_count=row.rating_count,
        seller_trusted=row.trusted,
        wholesale=wholesale,
    )


def _group(row: _Row) -> GroupFacts:
    return GroupFacts(
        slug=row.slug,
        product_type=row.product_type,
        status=row.status,
        source=row.source,
        support=row.support,
        retired=row.retired_at is not None,
        is_fallback=bool(row.is_fallback),
        liquidity_score=row.liquidity_score,
        pricing=row.pricing or {},
    )


async def _store(
    session: AsyncSession, deals: Sequence[tuple[_Row, Judgement]], now: datetime
) -> int:
    if not deals:
        return 0
    ids = [row.listing_id for row, _ in deals]
    known: set[int] = set()
    for start in range(0, len(ids), _CHUNK):
        known.update(
            await session.scalars(
                select(DealCandidate.listing_id).where(
                    DealCandidate.listing_id.in_(ids[start : start + _CHUNK])
                )
            )
        )
    for start in range(0, len(deals), _CHUNK):
        values = [_values(row, judgement, now) for row, judgement in deals[start : start + _CHUNK]]
        statement = insert(DealCandidate).values(values)
        refreshed = {
            name: statement.excluded[name]
            for name in values[0]
            if name not in {"listing_id", "found_at"}
        }
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[DealCandidate.listing_id], set_=refreshed
            )
        )
    return len(set(ids) - known)


def _values(row: _Row, judgement: Judgement, now: datetime) -> dict[str, Any]:
    outcome = judgement.resale
    assert outcome is not None and judgement.p25 is not None and judgement.p50 is not None
    return {
        "listing_id": row.listing_id,
        "group_id": row.group_id,
        "bucket": judgement.bucket,
        "price": row.price,
        "shipping": row.shipping,
        "p25": judgement.p25,
        "p50": judgement.p50,
        "net": outcome.net,
        "roi": outcome.roi,
        "liquidity_score": row.liquidity_score,
        "found_at": now,
        "updated_at": now,
    }


async def _drop(session: AsyncSession, listing_ids: Sequence[int]) -> int:
    dropped = 0
    for start in range(0, len(listing_ids), _CHUNK):
        result = await session.execute(
            delete(DealCandidate).where(
                DealCandidate.listing_id.in_(listing_ids[start : start + _CHUNK]),
                DealCandidate.verdict.is_(None),
            )
        )
        dropped += int(getattr(result, "rowcount", 0) or 0)
    return dropped


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
