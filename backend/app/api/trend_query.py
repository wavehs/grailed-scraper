"""Filters of the trends list, shared by ``GET /api/trends`` and its CSV export (v2 plan 3.6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Query
from sqlalchemy import ColumnElement, Select, Subquery, func, or_, select

from app.api.errors import ApiError
from app.api.trend_fields import LOW_DATA_SALES, Condition, Department
from app.api.trend_periods import MONTH_PATTERN, MonthRange, parse_period, period_totals
from app.db.models import Brand, GroupMetric, ModelGroup
from app.services.grouping.descriptors import DESCRIPTOR_PREFIX
from app.services.grouping.policy import REVIEW_TYPE

WINDOWS = (7, 30, 90)
Level = Literal["model", "type", "brand"]
SortKey = Literal["trend", "growth", "speed", "sales", "price", "supply", "new", "liquidity", "p30"]


@dataclass(frozen=True, slots=True)
class TrendQuery:
    level: Level = "model"
    brand_ids: str = ""
    section: str | None = None
    product_type: str | None = None
    window: int = 30
    month_from: str | None = None
    month_to: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    new_only: bool = False
    min_sales: int = 0
    condition: Condition = "all"
    department: Department | None = None
    hide_low_data: bool = False
    include_fallback: bool = False
    include_descriptors: bool = False
    search: str = ""
    sort: SortKey = "trend"
    desc: bool = True


def trend_query(
    level: Level = "model",
    brand_ids: Annotated[str, Query(max_length=500, pattern=r"^[0-9,]*$")] = "",
    section: Annotated[str | None, Query(max_length=32)] = None,
    # One type id or a comma-separated list (a wardrobe season preset).
    product_type: Annotated[str | None, Query(max_length=500, pattern=r"^[a-z0-9_,]*$")] = None,
    window: Annotated[int, Query()] = 30,
    month_from: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    month_to: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    price_min: Annotated[int | None, Query(ge=0)] = None,
    price_max: Annotated[int | None, Query(ge=0)] = None,
    new_only: bool = False,
    min_sales: Annotated[int, Query(ge=0, le=10_000)] = 0,
    condition: Condition = "all",
    department: Department | None = None,
    hide_low_data: bool = False,
    include_fallback: bool = False,
    include_descriptors: bool = False,
    search: Annotated[str, Query(max_length=200)] = "",
    sort: SortKey = "trend",
    desc: bool = True,
) -> TrendQuery:
    """The list's query parameters; an unknown window is rejected here."""

    check_window(window)
    return TrendQuery(
        level=level,
        brand_ids=brand_ids,
        section=section,
        product_type=product_type,
        window=window,
        month_from=month_from,
        month_to=month_to,
        price_min=price_min,
        price_max=price_max,
        new_only=new_only,
        min_sales=min_sales,
        condition=condition,
        department=department,
        hide_low_data=hide_low_data,
        include_fallback=include_fallback,
        include_descriptors=include_descriptors,
        search=search,
        sort=sort,
        desc=desc,
    )


@dataclass(frozen=True, slots=True)
class TrendSelect:
    """The filtered statement plus what the list needs to page, total and describe it."""

    statement: Select[Any]
    sold: Any
    price: Any
    # Brand, section and type filters alone: the "no model" share ignores the others.
    scope: list[ColumnElement[bool]]
    brand_ids: list[int]
    types: list[str]
    period: MonthRange | None
    totals: Subquery | None

    def ordered(self, query: TrendQuery) -> Select[Any]:
        return self.statement.order_by(*order(query.sort, query.desc, self.sold, self.price))


def trend_select(query: TrendQuery) -> TrendSelect:
    """Rows ``(metric, brand name, group, sold, price)`` matching ``query``."""

    period = parse_period(query.month_from, query.month_to)
    totals = period_totals(period, query.condition) if period else None
    sold: Any = sold_column(query.window)
    price: Any = price_column(query.window)
    if totals is not None:
        sold, price = func.coalesce(totals.c.sold, 0), totals.c.price
    ids = [int(value) for value in query.brand_ids.split(",") if value]
    types = [value for value in (query.product_type or "").split(",") if value]
    scope = _scope(query, ids, types)
    filters = [*_level_filters(query, bool(types)), *scope]
    filters.extend(_value_filters(query, sold, price, totals is not None))
    statement = (
        select(GroupMetric, Brand.name, ModelGroup, sold, price)
        .join(Brand, Brand.id == GroupMetric.brand_id)
        .outerjoin(ModelGroup, ModelGroup.id == GroupMetric.group_id)
        .where(*filters)
    )
    if totals is not None:
        statement = statement.outerjoin(totals, totals.c.scope_key == GroupMetric.scope_key)
    term = query.search.strip().casefold()
    if term:
        statement = statement.where(
            or_(
                func.lower(Brand.name).contains(term, autoescape=True),
                func.lower(ModelGroup.name).contains(term, autoescape=True),
            )
        )
    return TrendSelect(statement, sold, price, scope, ids, types, period, totals)


def _scope(query: TrendQuery, ids: list[int], types: list[str]) -> list[ColumnElement[bool]]:
    scope: list[ColumnElement[bool]] = []
    if ids:
        scope.append(GroupMetric.brand_id.in_(ids))
    if types:
        scope.append(GroupMetric.product_type.in_(types))
    if query.section:
        scope.append(GroupMetric.section == query.section)
    return scope


def _level_filters(query: TrendQuery, typed: bool) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [GroupMetric.scope == query.level]
    if query.level == "model":
        filters.append(GroupMetric.is_line.is_(True))
        # Retired groups keep their frozen metrics but leave the rating.
        filters.append(ModelGroup.retired_at.is_(None))
        if not query.include_fallback:
            filters.append(GroupMetric.is_fallback.is_(False))
        if not query.include_descriptors:
            # Descriptions ("Baggy", "Wide Leg") are no models: hidden unless asked for.
            filters.append(~ModelGroup.slug.startswith(DESCRIPTOR_PREFIX, autoescape=True))
    if not typed and query.level != "brand":
        filters.append(GroupMetric.product_type != REVIEW_TYPE)
    return filters


def _value_filters(
    query: TrendQuery, sold: Any, price: Any, by_period: bool
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    if query.price_min is not None:
        filters.append(price >= query.price_min)
    if query.price_max is not None:
        filters.append(price <= query.price_max)
    if query.new_only:
        filters.append(GroupMetric.is_new.is_(True))
    if query.min_sales:
        filters.append(sold >= query.min_sales)
    if query.hide_low_data:
        filters.append(GroupMetric.sold_90d >= LOW_DATA_SALES)
    if query.department:
        filters.append(GroupMetric.department == query.department)
    if query.condition != "all" and by_period:
        filters.append(sold > 0)
    elif query.condition != "all":
        # Only scopes with sales of that condition in the last 90 days.
        filters.append(func.json_extract(GroupMetric.pricing, f"$.{query.condition}.n") > 0)
    return filters


def check_window(window: int) -> None:
    if window not in WINDOWS:
        raise ApiError(422, "invalid_window", "window must be 7, 30 or 90 days")


def sold_column(window: int) -> Any:
    return {7: GroupMetric.sold_7d, 30: GroupMetric.sold_30d, 90: GroupMetric.sold_90d}[window]


def price_column(window: int) -> Any:
    return {
        7: GroupMetric.median_price_7d,
        30: GroupMetric.median_price_30d,
        90: GroupMetric.median_price_90d,
    }[window]


def order(sort: str, desc: bool, sold: Any, price: Any) -> list[Any]:
    columns: dict[str, Any] = {
        "trend": GroupMetric.trend_score,
        "growth": GroupMetric.growth,
        "sales": sold,
        "price": price,
        "supply": GroupMetric.active_now,
        "new": GroupMetric.first_seen_at,
        "liquidity": GroupMetric.liquidity_score,
        "p30": GroupMetric.p30,
    }
    if sort == "speed":
        # "Fastest first" means the shortest median time to sell.
        primary = GroupMetric.median_days_to_sell
        ordered = primary.asc() if desc else primary.desc()
    else:
        primary = columns[sort]
        ordered = primary.desc() if desc else primary.asc()
    return [ordered.nulls_last(), GroupMetric.sold_30d.desc(), GroupMetric.id]
