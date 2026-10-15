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
from sqlalchemy import String, cast, delete, func, select
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

    return WatchlistResponse(data=await _items(session))


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
    item.note = _note(body)
    await session.commit()
    return (await _items(session, group_id))[0]


@router.patch("/{group_id}", response_model=WatchItem)
async def change_note(
    group_id: int,
    body: WatchNote,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WatchItem:
    """Change the note of a followed model; unlike ``PUT`` it never adds one back."""

    item = await session.get(WatchlistItem, group_id)
    if item is None:
        raise ApiError(404, "not_watched", "The model is not on the watchlist")
    item.note = _note(body)
    await session.commit()
    return (await _items(session, group_id))[0]


@router.delete("/{group_id}", status_code=204)
async def unwatch(group_id: int, session: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    """Stop following a model; not following it already is no error."""

    await session.execute(delete(WatchlistItem).where(WatchlistItem.group_id == group_id))
    await session.commit()
    return Response(status_code=204)


def _note(body: WatchNote) -> str | None:
    return (body.note or "").strip() or None


async def _items(session: AsyncSession, group_id: int | None = None) -> list[WatchItem]:
    """Watched models (or one) with their numbers, in four queries however many there are."""

    query = (
        select(WatchlistItem, ModelGroup, Brand.name)
        .join(ModelGroup, ModelGroup.id == WatchlistItem.group_id)
        .join(Brand, Brand.id == ModelGroup.brand_id)
        .order_by(WatchlistItem.added_at.desc(), WatchlistItem.group_id)
    )
    if group_id is not None:
        query = query.where(WatchlistItem.group_id == group_id)
    rows = (await session.execute(query)).tuples().all()
    keys = [f"model:{group.id}" for _, group, _ in rows]
    metrics = {
        metric.scope_key: metric
        for metric in await session.scalars(
            select(GroupMetric).where(GroupMetric.scope_key.in_(keys))
        )
    }
    newer = await _nearest(session, group_id, older=False)
    baselines = {**newer, **await _nearest(session, group_id, older=True)}
    items = []
    for item, group, brand in rows:
        key = f"model:{group.id}"
        snapshot, metric = baselines.get(key), metrics.get(key)
        baseline = _snapshot_point(snapshot) if snapshot is not None else None
        current = _metric_point(metric) if metric is not None else None
        items.append(
            WatchItem(
                group_id=group.id,
                brand=brand,
                name=group.name,
                product_type=group.product_type,
                retired=group.retired_at is not None,
                added_at=_aware(item.added_at),
                note=item.note,
                baseline=baseline,
                current=current,
                changes=changes(baseline, current) if baseline and current else None,
            )
        )
    return items


async def _nearest(
    session: AsyncSession, group_id: int | None, *, older: bool
) -> dict[str, GroupMetricDaily]:
    """Per watched model, the last snapshot on or before the day it was added (``older``)
    or the first one after it."""

    day = func.date(WatchlistItem.added_at)
    snapshot = GroupMetricDaily.snapshot_date
    ranked = (
        select(
            GroupMetricDaily.id.label("id"),
            func.row_number()
            .over(
                partition_by=GroupMetricDaily.scope_key,
                order_by=snapshot.desc() if older else snapshot,
            )
            .label("rank"),
        )
        .join(
            WatchlistItem,
            GroupMetricDaily.scope_key == "model:" + cast(WatchlistItem.group_id, String),
        )
        .where(snapshot <= day if older else snapshot > day)
    )
    if group_id is not None:
        ranked = ranked.where(WatchlistItem.group_id == group_id)
    first = ranked.subquery()
    rows = await session.scalars(
        select(GroupMetricDaily)
        .join(first, first.c.id == GroupMetricDaily.id)
        .where(first.c.rank == 1)
    )
    return {row.scope_key: row for row in rows}


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
