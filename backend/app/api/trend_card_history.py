"""The model card's year: monthly sales and medians by condition, and the type's season profile.

v2 plan 3.4 (docs/METRICS.md §1a, §10). Months come from ``group_month_stats``; the running
month is included and flagged ``partial``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GroupMonthStat, SeasonProfile
from app.domain.listings import decimal_to_cents
from app.services.metrics.seasonality import month_range, month_start

CARD_MONTHS = 12
_YEAR = 12


class MonthPoint(BaseModel):
    month: str
    partial: bool
    sold: int
    sold_new: int
    sold_used: int
    p50: int | None
    p50_new: int | None
    p50_used: int | None


class SeasonOverlay(BaseModel):
    """``si_abs`` of the type per calendar month, January first (None = no data)."""

    profile_key: str
    si_abs: list[float | None]
    months_covered: int
    stable: bool | None


def card_months(today: date, count: int = CARD_MONTHS) -> list[date]:
    """The last ``count`` calendar months ending with the running one."""

    index = today.year * _YEAR + today.month - 1 - (count - 1)
    return month_range(date(index // _YEAR, index % _YEAR + 1, 1), month_start(today))


async def month_points(session: AsyncSession, scope_key: str, today: date) -> list[MonthPoint]:
    months = card_months(today)
    rows = await session.scalars(
        select(GroupMonthStat).where(
            GroupMonthStat.scope_key == scope_key, GroupMonthStat.month >= months[0]
        )
    )
    stats = {(row.month, row.cond_bucket): row for row in rows}
    running = month_start(today)

    def bucket(month: date, name: str) -> tuple[int, int | None]:
        row = stats.get((month, name))
        if row is None:
            return 0, None
        return row.sold_count, decimal_to_cents(row.p50) if row.p50 is not None else None

    points: list[MonthPoint] = []
    for month in months:
        sold, p50 = bucket(month, "all")
        sold_new, p50_new = bucket(month, "new")
        sold_used, p50_used = bucket(month, "used")
        points.append(
            MonthPoint(
                month=month.strftime("%Y-%m"),
                partial=month == running,
                sold=sold,
                sold_new=sold_new,
                sold_used=sold_used,
                p50=p50,
                p50_new=p50_new,
                p50_used=p50_used,
            )
        )
    return points


async def type_overlay(
    session: AsyncSession, product_type: str | None, department: str | None
) -> SeasonOverlay | None:
    """The department's profile of the type, else the type's over all departments."""

    if not product_type:
        return None
    keys = [f"type:{product_type}:{department}"] if department else []
    keys.append(f"type:{product_type}:*")
    profiles = {
        row.profile_key: row
        for row in await session.scalars(
            select(SeasonProfile).where(SeasonProfile.profile_key.in_(keys))
        )
    }
    profile = next((profiles[key] for key in keys if key in profiles), None)
    if profile is None:
        return None
    return SeasonOverlay(
        profile_key=profile.profile_key,
        si_abs=_by_month(profile.months),
        months_covered=profile.months_covered,
        stable=profile.stable,
    )


def _by_month(points: list[dict[str, Any]]) -> list[float | None]:
    values: list[float | None] = [None] * _YEAR
    for point in points:
        month = point.get("month")
        if isinstance(month, int) and 1 <= month <= _YEAR and point.get("si_abs") is not None:
            values[month - 1] = float(Decimal(str(point["si_abs"])))
    return values
