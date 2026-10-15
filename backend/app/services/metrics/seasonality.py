"""Seasonal profiles of sales by calendar month (v2 plan 2.5).

A profile averages each calendar month over the complete months a scope covers: the first
month of collected history and the running month are partial and left out. ``si_abs`` says
when a scope sells most; ``si_rel`` compares its share of a month with the market's share,
which removes the market-wide December peak and gaps in collection.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

SEASONS: dict[str, tuple[int, int, int]] = {
    "autumn": (9, 10, 11),
    "winter": (12, 1, 2),
    "spring": (3, 4, 5),
    "summer": (6, 7, 8),
}
SEASON_MIN_SALES = 50
# A model's own profile weighs n / (n + 300) against its type's.
MODEL_BLEND_PRIOR = 300
# Two years of months compare a profile with itself a year earlier.
STABLE_MIN_MONTHS = 24
STABLE_MIN_CORRELATION = 0.5
FORECAST_WINDOW_DAYS = 30
_YEAR = 12


@dataclass(frozen=True, slots=True)
class MonthPoint:
    month: int
    sales: float
    si_abs: float | None
    si_rel: float | None


@dataclass(frozen=True, slots=True)
class SeasonPoint:
    sales: float
    si_abs: float | None
    si_rel: float | None


@dataclass(frozen=True, slots=True)
class Profile:
    months: tuple[MonthPoint, ...]
    seasons: dict[str, SeasonPoint | None]
    months_covered: int
    sales_year: float
    correlation: float | None = None
    stable: bool | None = None
    blend_weight: float | None = None


def month_start(moment: datetime | date) -> date:
    return date(moment.year, moment.month, 1)


def next_month(month: date) -> date:
    return date(month.year + month.month // _YEAR, month.month % _YEAR + 1, 1)


def month_range(first: date, last: date) -> list[date]:
    """Months from ``first`` to ``last`` inclusive, both given as first days."""

    months: list[date] = []
    month = first
    while month <= last:
        months.append(month)
        month = next_month(month)
    return months


def complete_months(first_sale: datetime, as_of: datetime) -> list[date]:
    """Months after the one of the first collected sale and before the running one."""

    return month_range(next_month(month_start(first_sale)), _previous(month_start(as_of)))


def build_profile(
    counts: Mapping[date, int], months: Sequence[date], market: Mapping[date, int]
) -> Profile:
    """Profile of sales ``counts`` over the covered ``months`` (a month without sales is 0)."""

    covered = sorted(set(months))
    means = _calendar_means(counts, covered)
    market_means = _calendar_means(market, covered)
    total = sum(means.values())
    market_total = sum(market_means.values())
    average = total / len(means) if means else 0.0
    points = tuple(
        MonthPoint(
            month=month,
            sales=sales,
            si_abs=sales / average if average > 0 else None,
            si_rel=_relative(sales, total, market_means[month], market_total),
        )
        for month, sales in sorted(means.items())
    )
    correlation = _year_over_year(counts, covered)
    return Profile(
        months=points,
        seasons=_seasons(points),
        months_covered=len(covered),
        sales_year=total * _YEAR / len(means) if means else 0.0,
        correlation=correlation,
        stable=None if correlation is None else correlation >= STABLE_MIN_CORRELATION,
    )


def blend(own: Profile, parent: Profile, sales_year: float) -> Profile:
    """A model's profile pulled towards its type's; ``sales`` stay the model's own."""

    weight = sales_year / (sales_year + MODEL_BLEND_PRIOR)
    by_month = {point.month: point for point in parent.months}

    def mix(mine: float | None, theirs: float | None) -> float | None:
        if mine is None or theirs is None:
            return mine
        return weight * mine + (1 - weight) * theirs

    points = tuple(
        replace(
            point,
            si_abs=mix(point.si_abs, by_month[point.month].si_abs),
            si_rel=mix(point.si_rel, by_month[point.month].si_rel),
        )
        if point.month in by_month
        else point
        for point in own.months
    )
    return replace(own, months=points, seasons=_seasons(points), blend_weight=weight)


def forecast_sales(
    sold_30d: int, si_abs: Mapping[int, float], target: Sequence[int], as_of: datetime
) -> float | None:
    """Expected 30-day sales in the ``target`` months at the profile's seasonal level.

    The current level is the profile of the complete months the last 30 days overlap; when
    none has a value (a running month never complete before), the months around it.
    """

    start = as_of - timedelta(days=FORECAST_WINDOW_DAYS)
    overlapped = {month.month for month in month_range(month_start(start), month_start(as_of))}
    window = [si_abs[month] for month in sorted(overlapped) if month in si_abs]
    if not window:
        around = (_previous(month_start(as_of)).month, next_month(month_start(as_of)).month)
        window = [si_abs[month] for month in around if month in si_abs]
    wanted = [si_abs[month] for month in target if month in si_abs]
    level = sum(window) / len(window) if window else 0.0
    if not wanted or level <= 0:
        return None
    return sold_30d * (sum(wanted) / len(wanted)) / level


def _previous(month: date) -> date:
    return (
        date(month.year - 1, _YEAR, 1) if month.month == 1 else date(month.year, month.month - 1, 1)
    )


def _calendar_means(counts: Mapping[date, int], months: Sequence[date]) -> dict[int, float]:
    by_calendar: dict[int, list[int]] = {}
    for month in months:
        by_calendar.setdefault(month.month, []).append(counts.get(month, 0))
    return {month: sum(values) / len(values) for month, values in by_calendar.items()}


def _relative(sales: float, total: float, market: float, market_total: float) -> float | None:
    if total <= 0 or market <= 0 or market_total <= 0:
        return None
    return (sales / total) / (market / market_total)


def _seasons(points: Sequence[MonthPoint]) -> dict[str, SeasonPoint | None]:
    by_month = {point.month: point for point in points}
    seasons: dict[str, SeasonPoint | None] = {}
    for name, months in SEASONS.items():
        present = [by_month[month] for month in months if month in by_month]
        sales = sum(point.sales for point in present)
        seasons[name] = (
            SeasonPoint(sales, _mean(p.si_abs for p in present), _mean(p.si_rel for p in present))
            if present and sales >= SEASON_MIN_SALES
            else None
        )
    return seasons


def _mean(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) / len(known) if known else None


def _year_over_year(counts: Mapping[date, int], months: Sequence[date]) -> float | None:
    """Pearson correlation of the last twelve months with the twelve before them."""

    if len(months) < STABLE_MIN_MONTHS:
        return None
    recent = months[-STABLE_MIN_MONTHS:]
    earlier = [float(counts.get(month, 0)) for month in recent[:_YEAR]]
    later = [float(counts.get(month, 0)) for month in recent[_YEAR:]]
    return _pearson(earlier, later)


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    spread = math.sqrt(
        sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right)
    )
    return covariance / spread if spread > 0 else None
