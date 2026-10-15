"""Every seasonal profile, rebuilt from the sold listings after each recompute (plan 2.5).

Types are profiled across brands, per department and over all of them; a brand's type only
with 600 sales a year, a model only with 300, blended with its type's profile. A month
counts a brand's sales only once that brand's collected history covers it completely.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing, ListingModelAssignment, ModelGroup, SeasonProfile
from app.services.metrics.seasonality import (
    Profile,
    blend,
    build_profile,
    complete_months,
    month_start,
)

BRAND_MIN_SALES_YEAR = 600
MODEL_MIN_SALES_YEAR = 300
# A partial recompute leaves younger profiles alone: they span every brand's sales.
PROFILE_MAX_AGE = timedelta(days=1)
# The per-type "No model" bucket is not a model.
_NO_MODEL = "_none"
_INDEX = Decimal("0.0001")
_CENT = Decimal("0.01")
_INSERT_CHUNK = 500


@dataclass(frozen=True, slots=True)
class Sale:
    brand_id: int
    product_type: str | None
    department: str | None
    group_id: int | None
    sold_at: datetime


@dataclass(slots=True)
class _Counts:
    market: Counter[date]
    types: defaultdict[tuple[str, str | None], Counter[date]]
    brands: defaultdict[tuple[int, str], Counter[date]]
    models: defaultdict[int, Counter[date]]
    model_departments: defaultdict[int, Counter[str]]
    model_scopes: defaultdict[int, Counter[tuple[int, str]]]


async def profiles_due(session: AsyncSession, now: datetime) -> bool:
    """True when no profile exists or the newest is older than ``PROFILE_MAX_AGE``."""

    newest = await session.scalar(select(func.max(SeasonProfile.computed_at)))
    if newest is None:
        return True
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    return now - newest >= PROFILE_MAX_AGE


async def write_season_profiles(session: AsyncSession, as_of: datetime) -> int:
    """Replace every profile; the caller commits."""

    rows = profile_rows(await load_sales(session), as_of)
    await session.execute(delete(SeasonProfile))
    for start in range(0, len(rows), _INSERT_CHUNK):
        await session.execute(insert(SeasonProfile), rows[start : start + _INSERT_CHUNK])
    return len(rows)


async def load_sales(session: AsyncSession) -> list[Sale]:
    models = select(ModelGroup.id).where(
        ModelGroup.retired_at.is_(None),
        ModelGroup.status != "ignored",
        ModelGroup.slug != _NO_MODEL,
    )
    result = await session.execute(
        select(
            Listing.brand_id,
            Listing.product_type,
            Listing.department,
            ListingModelAssignment.model_group_id,
            Listing.sold_at,
        )
        .outerjoin(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
        .where(
            Listing.status == "sold",
            Listing.sold_at.is_not(None),
            Listing.brand_id.is_not(None),
        )
    )
    grouped = set((await session.scalars(models)).all())
    return [
        Sale(
            brand_id=brand_id,
            product_type=product_type,
            department=department,
            group_id=group_id if group_id in grouped else None,
            sold_at=sold_at if sold_at.tzinfo else sold_at.replace(tzinfo=UTC),
        )
        for brand_id, product_type, department, group_id, sold_at in result.tuples().all()
        if brand_id is not None and sold_at is not None
    ]


def profile_rows(sales: Sequence[Sale], as_of: datetime) -> list[dict[str, Any]]:
    """Rows of ``season_profiles`` for the sales known at ``as_of``."""

    covered = _coverage(sales, as_of)
    months = sorted(set().union(*covered.values())) if covered else []
    counts = _count(sales, covered)
    market = build_profile(counts.market, months, counts.market)
    rows = [_row("market", "market", market, as_of)]
    types: dict[tuple[str, str | None], Profile] = {}
    for (product_type, department), by_month in sorted(counts.types.items(), key=_type_order):
        profile = build_profile(by_month, months, counts.market)
        types[product_type, department] = profile
        key = f"type:{product_type}:{department or '*'}"
        rows.append(
            _row(key, "type", profile, as_of, product_type=product_type, department=department)
        )
    for (brand_id, product_type), by_month in sorted(counts.brands.items()):
        profile = build_profile(by_month, sorted(covered[brand_id]), counts.market)
        if profile.sales_year >= BRAND_MIN_SALES_YEAR:
            key = f"brand:{brand_id}:{product_type}"
            rows.append(_row(key, "brand", profile, as_of, brand_id, product_type=product_type))
    for group_id, by_month in sorted(counts.models.items()):
        brand_id, product_type = _majority_scope(counts.model_scopes[group_id])
        own = build_profile(by_month, sorted(covered[brand_id]), counts.market)
        if own.sales_year < MODEL_MIN_SALES_YEAR:
            continue
        department = _majority(counts.model_departments[group_id])
        parent = types.get((product_type, department)) or types[product_type, None]
        rows.append(
            _row(
                f"model:{group_id}",
                "model",
                blend(own, parent, own.sales_year),
                as_of,
                brand_id,
                group_id=group_id,
                product_type=product_type,
                department=department,
            )
        )
    return rows


def _coverage(sales: Iterable[Sale], as_of: datetime) -> dict[int, set[date]]:
    first: dict[int, datetime] = {}
    for sale in sales:
        if sale.brand_id not in first or sale.sold_at < first[sale.brand_id]:
            first[sale.brand_id] = sale.sold_at
    return {brand_id: set(complete_months(moment, as_of)) for brand_id, moment in first.items()}


def _count(sales: Iterable[Sale], covered: Mapping[int, set[date]]) -> _Counts:
    counts = _Counts(
        Counter(),
        defaultdict(Counter),
        defaultdict(Counter),
        defaultdict(Counter),
        defaultdict(Counter),
        defaultdict(Counter),
    )
    for sale in sales:
        month = month_start(sale.sold_at)
        if month not in covered[sale.brand_id]:
            continue
        counts.market[month] += 1
        if sale.product_type is None:
            continue
        counts.types[sale.product_type, None][month] += 1
        if sale.department is not None:
            counts.types[sale.product_type, sale.department][month] += 1
        counts.brands[sale.brand_id, sale.product_type][month] += 1
        if sale.group_id is not None:
            counts.models[sale.group_id][month] += 1
            counts.model_scopes[sale.group_id][sale.brand_id, sale.product_type] += 1
            if sale.department is not None:
                counts.model_departments[sale.group_id][sale.department] += 1
    return counts


def _type_order(item: tuple[tuple[str, str | None], object]) -> tuple[str, str]:
    (product_type, department), _ = item
    return product_type, department or ""


def _majority_scope(scopes: Counter[tuple[int, str]]) -> tuple[int, str]:
    """The brand and type most of a model's sales carry; ties go to the smallest."""

    return min(scopes, key=lambda scope: (-scopes[scope], scope))


def _majority(departments: Counter[str]) -> str | None:
    if not departments:
        return None
    return min(departments, key=lambda name: (-departments[name], name))


def _row(
    key: str,
    kind: str,
    profile: Profile,
    as_of: datetime,
    brand_id: int | None = None,
    *,
    group_id: int | None = None,
    product_type: str | None = None,
    department: str | None = None,
) -> dict[str, Any]:
    return {
        "profile_key": key,
        "kind": kind,
        "brand_id": brand_id,
        "group_id": group_id,
        "product_type": product_type,
        "department": department,
        "months": [
            {
                "month": point.month,
                "sales": _round(point.sales, _CENT),
                "si_abs": _round(point.si_abs, _INDEX),
                "si_rel": _round(point.si_rel, _INDEX),
            }
            for point in profile.months
        ],
        "seasons": {
            name: None
            if season is None
            else {
                "sales": _round(season.sales, _CENT),
                "si_abs": _round(season.si_abs, _INDEX),
                "si_rel": _round(season.si_rel, _INDEX),
            }
            for name, season in profile.seasons.items()
        },
        "months_covered": profile.months_covered,
        "sales_year": Decimal(str(profile.sales_year)).quantize(_CENT, ROUND_HALF_UP),
        "correlation": _decimal(profile.correlation),
        "stable": profile.stable,
        "blend_weight": _decimal(profile.blend_weight),
        "computed_at": as_of,
    }


def _round(value: float | None, step: Decimal) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(step, ROUND_HALF_UP))


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(_INDEX, ROUND_HALF_UP)
