"""Cross-brand pass: smoothed P30 (v2 plan 2.2).

Runs once after every brand's raw counts are stored, because the smoothing parents (a type
across all brands) span brands. The Liquidity Score is the policy ranking that follows it
(``MetricsService.rank``). Retired groups keep their frozen metrics.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy import Table, bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GroupMetric, GroupMetricDaily, ModelGroup
from app.services.metrics.p30 import smooth

_UPDATE_CHUNK = 500


@dataclass(frozen=True, slots=True)
class RankRow:
    id: int
    scope: str
    brand_id: int
    product_type: str | None
    p30_sold: int
    p30_cohort: int
    retired: bool = False


def rank(rows: Sequence[RankRow]) -> dict[int, Decimal]:
    """Smoothed P30 for every live row."""

    return _smoothed([row for row in rows if not row.retired])


def _smoothed(rows: Sequence[RankRow]) -> dict[int, Decimal]:
    brands = [row for row in rows if row.scope == "brand"]
    overall = _rate(brands, Decimal(0))
    types: dict[str, list[RankRow]] = defaultdict(list)
    for row in rows:
        if row.scope == "type" and row.product_type:
            types[row.product_type].append(row)
    across = {name: _rate(items, overall, smoothed=True) for name, items in types.items()}
    in_brand = {
        (row.brand_id, row.product_type): smooth(
            row.p30_sold, row.p30_cohort, across.get(row.product_type or "", overall)
        )
        for row in rows
        if row.scope == "type"
    }
    result: dict[int, Decimal] = {}
    for row in rows:
        if row.scope == "type":
            result[row.id] = in_brand[(row.brand_id, row.product_type)]
            continue
        parent = overall
        if row.scope == "model":
            parent = in_brand.get(
                (row.brand_id, row.product_type), across.get(row.product_type or "", overall)
            )
        result[row.id] = smooth(row.p30_sold, row.p30_cohort, parent)
    return result


def _rate(rows: Iterable[RankRow], parent: Decimal, *, smoothed: bool = False) -> Decimal:
    sold = cohort = 0
    for row in rows:
        sold += row.p30_sold
        cohort += row.p30_cohort
    if smoothed:
        return smooth(sold, cohort, parent)
    return Decimal(sold) / Decimal(cohort) if cohort else parent


def quantile(values: Sequence[Decimal], q: Decimal) -> Decimal:
    """Linear interpolation between closest ranks; 0 for an empty sample."""

    if not values:
        return Decimal(0)
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


async def rank_metrics(session: AsyncSession, day: date) -> int:
    """Store smoothed P30 on ``group_metrics`` and on the day's snapshots."""

    statement = select(
        GroupMetric.id,
        GroupMetric.scope_key,
        GroupMetric.scope,
        GroupMetric.brand_id,
        GroupMetric.product_type,
        GroupMetric.p30_sold,
        GroupMetric.p30_cohort,
        ModelGroup.retired_at,
    ).outerjoin(ModelGroup, ModelGroup.id == GroupMetric.group_id)
    rows: list[RankRow] = []
    keys: dict[int, str] = {}
    for row in (await session.execute(statement)).tuples().all():
        keys[row[0]] = row[1]
        rows.append(
            RankRow(
                id=row[0],
                scope=row[2],
                brand_id=row[3],
                product_type=row[4],
                p30_sold=row[5] or 0,
                p30_cohort=row[6] or 0,
                retired=row[7] is not None,
            )
        )
    ranked = rank(rows)
    values = [{"id": row_id, "p30": p30} for row_id, p30 in ranked.items()]
    for start in range(0, len(values), _UPDATE_CHUNK):
        await session.execute(update(GroupMetric), values[start : start + _UPDATE_CHUNK])
    await _update_snapshots(session, day, {keys[row_id]: p30 for row_id, p30 in ranked.items()})
    return len(values)


async def _update_snapshots(session: AsyncSession, day: date, ranked: dict[str, Decimal]) -> None:
    table = cast(Table, GroupMetricDaily.__table__)
    statement = (
        update(table)
        .where(table.c.scope_key == bindparam("key"), table.c.snapshot_date == day)
        .values(p30=bindparam("new_p30"))
    )
    values = [{"key": key, "new_p30": p30} for key, p30 in ranked.items()]
    connection = await session.connection()
    for start in range(0, len(values), _UPDATE_CHUNK):
        await connection.execute(statement, values[start : start + _UPDATE_CHUNK])
