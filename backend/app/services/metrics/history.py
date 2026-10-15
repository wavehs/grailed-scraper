"""Metric history: a snapshot per scope and day, and sales per scope, month and condition.

``group_metrics`` is overwritten on every recompute; snapshots keep a copy per day (v2 plan
1.3). Monthly stats (1.4) are rebuilt from every sale with the metrics: about 54k sales.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GroupMetricDaily, GroupMonthStat
from app.services.metrics.calculator import MetricListing, price_outliers

SNAPSHOT_KEEP_DAYS = 730
# A model without a sale in 90 days is kept only while its supply is worth watching.
SNAPSHOT_MIN_ACTIVE = 20
SNAPSHOT_COLUMNS = (
    "scope_key",
    "scope",
    "brand_id",
    "group_id",
    "sold_30d",
    "sold_90d",
    "active_now",
    "median_price_30d",
    "sell_through_30d",
    "trend_score",
)
BUCKETS = ("all", "new", "used")
QUARTILES = (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"))
_CENT = Decimal("0.01")
_INSERT_CHUNK = 500


@dataclass(frozen=True, slots=True)
class MonthStat:
    month: date
    bucket: str
    sold_count: int
    gmv: Decimal
    p25: Decimal | None
    p50: Decimal | None
    p75: Decimal | None


def percentile(values: Sequence[Decimal], q: Decimal) -> Decimal | None:
    """Linear interpolation between closest ranks (``numpy.percentile``'s default)."""

    if not values:
        return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    value = ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return value.quantize(_CENT, ROUND_HALF_UP)


def month_stats(items: Iterable[MetricListing], as_of: datetime | None = None) -> list[MonthStat]:
    """Sales by UTC calendar month; ``new`` is the ``is_new`` condition, ``used`` the rest.

    Every sale counts; GMV and quartiles leave out lots (``price_usable``) and sales far
    from the month's own prices (the scope outlier rule, applied per month).
    """

    by_month: dict[date, list[MetricListing]] = defaultdict(list)
    for item in items:
        if item.status != "sold" or item.sold_at is None:
            continue
        if as_of is not None and item.sold_at > as_of:
            continue
        by_month[_month(item.sold_at)].append(item)
    rows: list[MonthStat] = []
    for month, sales in sorted(by_month.items()):
        outliers = price_outliers(sales, _next_month(month))
        for bucket in BUCKETS:
            chosen = [item for item in sales if _in_bucket(item, bucket)]
            if not chosen:
                continue
            prices = [
                item.sale_price for item in chosen if item.price_usable and item.id not in outliers
            ]
            p25, p50, p75 = (percentile(prices, q) for q in QUARTILES)
            gmv = sum(prices, Decimal(0)).quantize(_CENT, ROUND_HALF_UP)
            rows.append(MonthStat(month, bucket, len(chosen), gmv, p25, p50, p75))
    return rows


def snapshot_rows(values: Iterable[Mapping[str, Any]], day: date) -> list[dict[str, Any]]:
    """Snapshot rows from fresh ``group_metrics`` rows; quiet models are left out."""

    return [
        {"snapshot_date": day, **{column: row.get(column) for column in SNAPSHOT_COLUMNS}}
        for row in values
        if row["scope"] != "model"
        or row["sold_90d"] >= 1
        or row["active_now"] >= SNAPSHOT_MIN_ACTIVE
    ]


async def write_snapshots(
    session: AsyncSession, brand_id: int, values: Iterable[Mapping[str, Any]], day: date
) -> int:
    """Replace the brand's snapshot of ``day`` and drop the ones past two years."""

    await session.execute(
        delete(GroupMetricDaily).where(
            GroupMetricDaily.brand_id == brand_id,
            (GroupMetricDaily.snapshot_date == day)
            | (GroupMetricDaily.snapshot_date < day - timedelta(days=SNAPSHOT_KEEP_DAYS)),
        )
    )
    rows = snapshot_rows(values, day)
    await _insert(session, GroupMetricDaily, rows)
    return len(rows)


async def write_month_stats(
    session: AsyncSession,
    brand_id: int,
    by_scope: Mapping[str, Sequence[MetricListing]],
    scope_meta: Mapping[str, Mapping[str, Any]],
    as_of: datetime,
    now: datetime,
) -> int:
    """Rebuild every monthly row of the brand."""

    await session.execute(delete(GroupMonthStat).where(GroupMonthStat.brand_id == brand_id))
    rows = [
        {
            "scope_key": key,
            "scope": scope_meta[key]["scope"],
            "brand_id": brand_id,
            "group_id": scope_meta[key].get("group_id"),
            "product_type": scope_meta[key].get("product_type"),
            "month": stat.month,
            "cond_bucket": stat.bucket,
            "sold_count": stat.sold_count,
            "gmv": stat.gmv,
            "p25": stat.p25,
            "p50": stat.p50,
            "p75": stat.p75,
            "computed_at": now,
        }
        for key, items in by_scope.items()
        for stat in month_stats(items, as_of)
    ]
    await _insert(session, GroupMonthStat, rows)
    return len(rows)


async def _insert(session: AsyncSession, table: type[Any], rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), _INSERT_CHUNK):
        await session.execute(insert(table), rows[start : start + _INSERT_CHUNK])


def _in_bucket(item: MetricListing, bucket: str) -> bool:
    if bucket == "all":
        return True
    return item.new_condition == (bucket == "new")


def _month(moment: datetime) -> date:
    return moment.astimezone(UTC).date().replace(day=1)


def _next_month(month: date) -> datetime:
    following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return datetime(following.year, following.month, 1, tzinfo=UTC)
