"""Calendar-month periods of the trends list (v2 plan 3.2, docs/METRICS.md §4).

A period replaces the 7/30/90-day window: sales and price come from ``group_month_stats``,
and the response says which of its months the collected data covers.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel
from sqlalchemy import Subquery, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.trend_fields import Condition
from app.db.models import GroupMonthStat, Listing
from app.services.metrics.seasonality import month_range, month_start

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
MAX_PERIOD_MONTHS = 24
# Grailed opened in 2013; the bounds only keep date arithmetic away from year 0 and 9999.
MIN_YEAR, MAX_YEAR = 2000, 2100
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class MonthRange:
    """Calendar months from ``first`` to ``last`` inclusive, both first days."""

    first: date
    last: date

    @property
    def months(self) -> list[date]:
        return month_range(self.first, self.last)


class PartialMonth(BaseModel):
    month: str
    from_day: int
    to_day: int


class PeriodCoverage(BaseModel):
    """Which months of the period the collected sales cover.

    ``data_from`` is the first collected sale of the selected brands, ``data_to`` the day of
    the last recompute; ``late_brands`` counts brands whose sales start in a later month
    than the period's first covered one.
    """

    month_from: str
    month_to: str
    data_from: date | None
    data_to: date | None
    missing: list[str]
    partial: list[PartialMonth]
    late_brands: int


def parse_period(month_from: str | None, month_to: str | None) -> MonthRange | None:
    """The requested months; ``None`` when the list keeps its day window."""

    if month_from is None and month_to is None:
        return None
    if month_from is None or month_to is None:
        raise ApiError(422, "invalid_period", "month_from and month_to go together")
    period = MonthRange(_month(month_from), _month(month_to))
    if period.first > period.last:
        raise ApiError(422, "invalid_period", "month_from must not be after month_to")
    if len(period.months) > MAX_PERIOD_MONTHS:
        raise ApiError(422, "invalid_period", f"a period spans at most {MAX_PERIOD_MONTHS} months")
    return period


def _month(value: str) -> date:
    year, month = (int(part) for part in value.split("-"))
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ApiError(422, "invalid_period", f"years run from {MIN_YEAR} to {MAX_YEAR}")
    return date(year, month, 1)


def period_totals(period: MonthRange, condition: Condition) -> Subquery:
    """Sales and price of every scope over the period in one condition bucket.

    The price is the average of the monthly medians weighted by sales: medians of months
    do not add up to the period's median, so the list marks it as approximate.
    """

    priced = case((GroupMonthStat.p50.is_not(None), GroupMonthStat.sold_count), else_=0)
    weighted = func.sum(GroupMonthStat.p50 * GroupMonthStat.sold_count)
    return (
        select(
            GroupMonthStat.scope_key.label("scope_key"),
            func.sum(GroupMonthStat.sold_count).label("sold"),
            (weighted / func.nullif(func.sum(priced), 0)).label("price"),
        )
        .where(
            GroupMonthStat.month.between(period.first, period.last),
            GroupMonthStat.cond_bucket == condition,
        )
        .group_by(GroupMonthStat.scope_key)
        .subquery("period")
    )


def period_price(value: object) -> Decimal | None:
    """Dollars of a ``period_totals`` price, which SQLite returns as a float."""

    if value is None:
        return None
    return Decimal(str(value)).quantize(_CENT, ROUND_HALF_UP)


async def period_coverage(
    session: AsyncSession,
    period: MonthRange,
    brand_ids: list[int],
    computed_at: datetime | None,
) -> PeriodCoverage:
    statement = (
        select(func.min(Listing.sold_at))
        .where(
            Listing.status == "sold",
            Listing.sold_at.is_not(None),
            Listing.brand_id.is_not(None),
        )
        .group_by(Listing.brand_id)
    )
    if brand_ids:
        statement = statement.where(Listing.brand_id.in_(brand_ids))
    firsts = [_utc_day(moment) for moment in (await session.scalars(statement)).all() if moment]
    data_from = min(firsts) if firsts else None
    data_to = _utc_day(computed_at) if computed_at else None
    return month_coverage(period, data_from, data_to, late=_late_brands(period, firsts))


def _late_brands(period: MonthRange, firsts: list[date]) -> int:
    """Brands whose sales start in a later month than the period's first covered one."""

    if not firsts:
        return 0
    start = month_start(max(period.first, min(firsts)))
    return sum(month_start(day) > start for day in firsts)


def month_coverage(
    period: MonthRange, data_from: date | None, data_to: date | None, *, late: int = 0
) -> PeriodCoverage:
    missing: list[str] = []
    partial: list[PartialMonth] = []
    for month in period.months:
        last_day = calendar.monthrange(month.year, month.month)[1]
        end = month.replace(day=last_day)
        if data_from is None or data_to is None or end < data_from or month > data_to:
            missing.append(_label(month))
            continue
        from_day = data_from.day if month == month_start(data_from) else 1
        to_day = data_to.day if month == month_start(data_to) else last_day
        if from_day > 1 or to_day < last_day:
            partial.append(PartialMonth(month=_label(month), from_day=from_day, to_day=to_day))
    return PeriodCoverage(
        month_from=_label(period.first),
        month_to=_label(period.last),
        data_from=data_from,
        data_to=data_to,
        missing=missing,
        partial=partial,
        late_brands=late,
    )


def _label(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def _utc_day(moment: datetime) -> date:
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(UTC).date()
