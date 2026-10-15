"""Model badges, assigned after the cross-brand pass (v2 plan 3.5).

* ``low_data`` (❔): fewer than five sales in 90 days; no other badge then.
* ``hot`` (🔥): Liquidity Score at or above the reference p90, with ten sales.
* ``stable`` (💎): thirty sales and a sale-price IQR within 35% of the median.
* ``slow`` (🐢): median days to sell at or above the reference p75 and smoothed P30 at
  or below its p25.
* ``saturated`` (⚠️): fresh asks per month of sales at or above the reference p80.

The reference sample is the one the model's score used (its section or all models, D10):
live, non-fallback models with five sales in 90 days. Retired models keep frozen badges.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GroupMetric, ModelGroup
from app.services.metrics.ranking import REFERENCE_MIN_SALES, SECTION, quantile

LOW_DATA = "low_data"
HOT = "hot"
STABLE = "stable"
SLOW = "slow"
SATURATED = "saturated"
HOT_QUANTILE = Decimal("0.9")
HOT_MIN_SALES = 10
STABLE_MIN_SALES = 30
STABLE_MAX_SPREAD = Decimal("0.35")
SLOW_DAYS_QUANTILE = Decimal("0.75")
SLOW_P30_QUANTILE = Decimal("0.25")
SATURATION_QUANTILE = Decimal("0.8")
MONTHS_IN_90D = 3
_UPDATE_CHUNK = 500


@dataclass(frozen=True, slots=True)
class BadgeRow:
    id: int
    section: str | None
    sold_90d: int
    ls_ref_scope: str | None = None
    liquidity_score: Decimal | None = None
    p30: Decimal | None = None
    median_days_to_sell: Decimal | None = None
    active_fresh: int | None = None
    # Interquartile range of all-condition sale prices over their median.
    spread: Decimal | None = None
    fallback: bool = False


@dataclass(frozen=True, slots=True)
class Thresholds:
    hot: Decimal | None
    slow_days: Decimal | None
    slow_p30: Decimal | None
    saturated: Decimal | None


def saturation(row: BadgeRow) -> Decimal | None:
    """Fresh asks per month of sales; None without sales or supply data."""

    if row.active_fresh is None or row.sold_90d <= 0:
        return None
    return Decimal(row.active_fresh) * MONTHS_IN_90D / Decimal(row.sold_90d)


def spread(pricing: dict[str, Any] | None) -> Decimal | None:
    """``(p75 - p25) / p50`` of the all-condition bucket; None when a quartile is missing."""

    bucket = (pricing or {}).get("all") or {}
    values = [bucket.get(key) for key in ("p25", "p50", "p75")]
    if any(value is None for value in values):
        return None
    p25, p50, p75 = (Decimal(str(value)) for value in values)
    return (p75 - p25) / p50 if p50 > 0 else None


def thresholds(sample: Sequence[BadgeRow]) -> Thresholds:
    def level(value: Callable[[BadgeRow], Decimal | None], q: Decimal) -> Decimal | None:
        present = [item for item in map(value, sample) if item is not None]
        return quantile(present, q) if present else None

    return Thresholds(
        hot=level(lambda row: row.liquidity_score, HOT_QUANTILE),
        slow_days=level(lambda row: row.median_days_to_sell, SLOW_DAYS_QUANTILE),
        slow_p30=level(lambda row: row.p30, SLOW_P30_QUANTILE),
        saturated=level(saturation, SATURATION_QUANTILE),
    )


def badges_of(row: BadgeRow, limits: Thresholds) -> list[str]:
    if row.sold_90d < REFERENCE_MIN_SALES:
        return [LOW_DATA]
    checks = (
        (HOT, row.sold_90d >= HOT_MIN_SALES and _reaches(row.liquidity_score, limits.hot)),
        (
            STABLE,
            row.sold_90d >= STABLE_MIN_SALES
            and row.spread is not None
            and row.spread <= STABLE_MAX_SPREAD,
        ),
        (
            SLOW,
            _reaches(row.median_days_to_sell, limits.slow_days)
            and row.p30 is not None
            and limits.slow_p30 is not None
            and row.p30 <= limits.slow_p30,
        ),
        (SATURATED, _reaches(saturation(row), limits.saturated)),
    )
    return [code for code, hit in checks if hit]


def _reaches(value: Decimal | None, limit: Decimal | None) -> bool:
    """``value`` at or above ``limit``; zero never earns a badge."""

    return value is not None and limit is not None and value > 0 and value >= limit


def assign_badges(rows: Sequence[BadgeRow]) -> dict[int, list[str]]:
    """Badges of every live model; fallback rows get none."""

    models = [row for row in rows if not row.fallback]
    sample = [row for row in models if row.sold_90d >= REFERENCE_MIN_SALES]
    overall = thresholds(sample)
    by_section: dict[str | None, list[BadgeRow]] = defaultdict(list)
    for row in sample:
        by_section[row.section].append(row)
    sections = {name: thresholds(items) for name, items in by_section.items()}
    result: dict[int, list[str]] = {row.id: [] for row in rows if row.fallback}
    for row in models:
        own = row.ls_ref_scope == SECTION and row.section in sections
        result[row.id] = badges_of(row, sections[row.section] if own else overall)
    return result


async def write_badges(session: AsyncSession) -> int:
    """Store the badges of every live model on ``group_metrics``."""

    statement = (
        select(
            GroupMetric.id,
            GroupMetric.section,
            GroupMetric.sold_90d,
            GroupMetric.ls_ref_scope,
            GroupMetric.liquidity_score,
            GroupMetric.p30,
            GroupMetric.median_days_to_sell,
            GroupMetric.active_fresh,
            GroupMetric.pricing,
            GroupMetric.is_fallback,
        )
        .outerjoin(ModelGroup, ModelGroup.id == GroupMetric.group_id)
        .where(GroupMetric.scope == "model", ModelGroup.retired_at.is_(None))
    )
    rows = [
        BadgeRow(
            id=row[0],
            section=row[1],
            sold_90d=row[2],
            ls_ref_scope=row[3],
            liquidity_score=row[4],
            p30=row[5],
            median_days_to_sell=row[6],
            active_fresh=row[7],
            spread=spread(row[8]),
            fallback=row[9],
        )
        for row in (await session.execute(statement)).tuples().all()
    ]
    values = [{"id": key, "badges": codes} for key, codes in assign_badges(rows).items()]
    for start in range(0, len(values), _UPDATE_CHUNK):
        await session.execute(update(GroupMetric), values[start : start + _UPDATE_CHUNK])
    return len(values)
