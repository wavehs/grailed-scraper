"""Watchlist: followed models and how they changed since being added (v2 plan 3.7).

The baseline is the model's last daily snapshot on or before the day it was added (the
first one after when none is older); "now" is its current ``group_metrics`` row.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.mutation import MutationRoute
from app.db.models import Brand, GroupMetric, GroupMetricDaily, ModelGroup, WatchlistItem
from app.db.session import get_db
from app.domain.listings import decimal_to_cents

router = APIRouter(prefix="/watchlist", tags=["watchlist"], route_class=MutationRoute)
WATCHLIST_MAX = 500
_RATIO = Decimal("0.0001")


class WatchPoint(BaseModel):
    """The model's numbers on ``as_of``; ``median_price`` is the 30-day median in cents."""

    as_of: date
    liquidity_score: Decimal | None
    median_price: int | None
    sold_30d: int
    active_now: int
    active_effective: int | None


class WatchChanges(BaseModel):
    """Now minus the baseline; ``median_price`` is a ratio (0.1 = 10% dearer)."""

    liquidity_score: Decimal | None
    median_price: Decimal | None
    sold_30d: int
    active_now: int
    active_effective: int | None


class WatchItem(BaseModel):
    group_id: int
    brand: str
    name: str
    product_type: str | None
    retired: bool
    added_at: datetime
    note: str | None
    baseline: WatchPoint | None
    current: WatchPoint | None
    changes: WatchChanges | None


class WatchlistResponse(BaseModel):
    data: list[WatchItem]


class WatchNote(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.get("", response_model=WatchlistResponse)
async def list_watchlist(session: Annotated[AsyncSession, Depends(get_db)]) -> WatchlistResponse:
    """Followed models, the most recently added first."""

    rows = (
        await session.execute(
            select(WatchlistItem, ModelGroup, Brand.name)
            .join(ModelGroup, ModelGroup.id == WatchlistItem.group_id)
            .join(Brand, Brand.id == ModelGroup.brand_id)
            .order_by(WatchlistItem.added_at.desc(), WatchlistItem.group_id)
        )
    ).tuples()
    return WatchlistResponse(
        data=[await _item(session, item, group, brand) for item, group, brand in rows]
    )


@router.put("/{group_id}", response_model=WatchItem)
async def watch(
    group_id: int,
    body: WatchNote,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WatchItem:
    """Follow a model, or change the note of one already followed (``added_at`` stays)."""

    group = await session.get(ModelGroup, group_id)
    if group is None:
        raise ApiError(404, "group_not_found", "Group does not exist")
    item = await session.get(WatchlistItem, group_id)
    if item is None:
        count = int(await session.scalar(select(func.count()).select_from(WatchlistItem)) or 0)
        if count >= WATCHLIST_MAX:
            raise ApiError(409, "watchlist_full", f"The watchlist holds {WATCHLIST_MAX} models")
        item = WatchlistItem(group_id=group_id, added_at=datetime.now(UTC), note=None)
        session.add(item)
    item.note = (body.note or "").strip() or None
    await session.commit()
    brand = await session.get(Brand, group.brand_id)
    return await _item(session, item, group, brand.name if brand else "")


@router.delete("/{group_id}", status_code=204)
async def unwatch(group_id: int, session: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    """Stop following a model; not following it already is no error."""

    await session.execute(delete(WatchlistItem).where(WatchlistItem.group_id == group_id))
    await session.commit()
    return Response(status_code=204)


async def _item(
    session: AsyncSession, item: WatchlistItem, group: ModelGroup, brand: str
) -> WatchItem:
    key = f"model:{group.id}"
    added = _aware(item.added_at)
    snapshot = await _baseline(session, key, added.date())
    metric = await session.scalar(select(GroupMetric).where(GroupMetric.scope_key == key))
    baseline = _snapshot_point(snapshot) if snapshot is not None else None
    current = _metric_point(metric) if metric is not None else None
    return WatchItem(
        group_id=group.id,
        brand=brand,
        name=group.name,
        product_type=group.product_type,
        retired=group.retired_at is not None,
        added_at=added,
        note=item.note,
        baseline=baseline,
        current=current,
        changes=changes(baseline, current) if baseline and current else None,
    )


async def _baseline(session: AsyncSession, key: str, day: date) -> GroupMetricDaily | None:
    older = await session.scalar(
        select(GroupMetricDaily)
        .where(GroupMetricDaily.scope_key == key, GroupMetricDaily.snapshot_date <= day)
        .order_by(GroupMetricDaily.snapshot_date.desc())
        .limit(1)
    )
    if older is not None:
        return older
    newer: GroupMetricDaily | None = await session.scalar(
        select(GroupMetricDaily)
        .where(GroupMetricDaily.scope_key == key, GroupMetricDaily.snapshot_date > day)
        .order_by(GroupMetricDaily.snapshot_date)
        .limit(1)
    )
    return newer


def _snapshot_point(row: GroupMetricDaily) -> WatchPoint:
    return WatchPoint(
        as_of=row.snapshot_date,
        liquidity_score=row.liquidity_score,
        median_price=_cents(row.median_price_30d),
        sold_30d=row.sold_30d,
        active_now=row.active_now,
        active_effective=row.active_effective,
    )


def _metric_point(row: GroupMetric) -> WatchPoint:
    return WatchPoint(
        as_of=_aware(row.computed_at).date(),
        liquidity_score=row.liquidity_score,
        median_price=_cents(row.median_price_30d),
        sold_30d=row.sold_30d,
        active_now=row.active_now,
        active_effective=row.active_effective,
    )


def changes(baseline: WatchPoint, current: WatchPoint) -> WatchChanges:
    """Differences; a ratio or difference needs both sides (and a positive old price)."""

    score = None
    if baseline.liquidity_score is not None and current.liquidity_score is not None:
        score = current.liquidity_score - baseline.liquidity_score
    price = None
    if baseline.median_price and current.median_price is not None:
        ratio = Decimal(current.median_price) / Decimal(baseline.median_price) - 1
        price = ratio.quantize(_RATIO)
    effective = None
    if baseline.active_effective is not None and current.active_effective is not None:
        effective = current.active_effective - baseline.active_effective
    return WatchChanges(
        liquidity_score=score,
        median_price=price,
        sold_30d=current.sold_30d - baseline.sold_30d,
        active_now=current.active_now - baseline.active_now,
        active_effective=effective,
    )


def _cents(value: Decimal | None) -> int | None:
    return decimal_to_cents(value) if value is not None else None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
