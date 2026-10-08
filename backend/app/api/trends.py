"""Trends: which models sell faster, more often and at better prices right now."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.groups import GroupDetail, group_detail_data
from app.db.models import Brand, GroupMetric, Listing, ListingModelAssignment, ModelGroup
from app.db.session import get_db
from app.domain.listings import decimal_to_cents
from app.services.grouping.descriptors import DESCRIPTOR_PREFIX
from app.services.grouping.kinds import GroupKind, group_kind
from app.services.grouping.policy import REVIEW_TYPE

router = APIRouter(prefix="/trends", tags=["trends"])
WINDOWS = (7, 30, 90)
SortKey = Literal["trend", "growth", "speed", "sales", "price", "supply", "new"]


class TrendRow(BaseModel):
    scope: Literal["model", "type", "brand"]
    scope_key: str
    group_id: int | None
    brand_id: int
    brand: str
    product_type: str | None
    section: str | None
    name: str | None
    status: str | None
    is_fallback: bool
    # The kind of the row's group; type and brand rows have none.
    kind: GroupKind | None = None
    versions: int = 0
    listings: int
    sold: int
    sold_7d: int
    sold_30d: int
    sold_prev_30d: int
    sold_90d: int
    growth: Decimal
    speed: Decimal | None
    trend_score: Decimal | None
    median_days_to_sell: Decimal | None
    sell_through_30d: Decimal
    median_price: int | None
    price_change: Decimal | None
    active_now: int
    new_listings_14d: int
    is_new: bool
    first_seen_at: datetime | None
    weekly_sales: list[int]


class NoModelShare(BaseModel):
    """"No model" against the whole scope (brands, section, type; other filters ignored).

    ``sold`` counts sales in the requested window; a share is ``None`` when its total is 0.
    """

    listings: int
    total_listings: int
    sold: int
    total_sold: int
    listings_share: Decimal | None
    sold_share: Decimal | None


class TrendListResponse(BaseModel):
    data: list[TrendRow]
    total: int
    computed_at: datetime | None
    no_model: NoModelShare


class VariantRow(BaseModel):
    value: str
    sold: int
    active: int
    sell_through: Decimal


class SaleRow(BaseModel):
    id: int
    grailed_id: int
    url: str
    title: str
    price: int
    status: str
    sold_at: datetime | None
    created_at: datetime | None
    days_to_sell: int | None
    size: str | None
    color: str | None
    group_id: int | None
    group_name: str | None
    kind: GroupKind | None
    relisted: bool


class TrendCard(BaseModel):
    group: GroupDetail
    metrics: TrendRow | None
    computed_at: datetime | None
    weekly_median_price: list[int | None]
    colors: list[VariantRow]
    sizes: list[VariantRow]
    versions: list[TrendRow]
    type_metrics: TrendRow | None
    recent_sales: list[SaleRow]
    active_listings: list[SaleRow]


@router.get("", response_model=TrendListResponse)
async def list_trends(
    session: Annotated[AsyncSession, Depends(get_db)],
    level: Literal["model", "type", "brand"] = "model",
    brand_ids: Annotated[str, Query(max_length=500, pattern=r"^[0-9,]*$")] = "",
    section: Annotated[str | None, Query(max_length=32)] = None,
    product_type: Annotated[str | None, Query(max_length=32)] = None,
    window: Annotated[int, Query()] = 30,
    price_min: Annotated[int | None, Query(ge=0)] = None,
    price_max: Annotated[int | None, Query(ge=0)] = None,
    new_only: bool = False,
    min_sales: Annotated[int, Query(ge=0, le=10_000)] = 0,
    include_fallback: bool = False,
    include_descriptors: bool = False,
    search: Annotated[str, Query(max_length=200)] = "",
    sort: SortKey = "trend",
    desc: bool = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TrendListResponse:
    _check_window(window)
    sold_column = _sold_column(window)
    price_column = _price_column(window)
    filters: list[ColumnElement[bool]] = [GroupMetric.scope == level]
    scope: list[ColumnElement[bool]] = []
    if level == "model":
        filters.append(GroupMetric.is_line.is_(True))
        # Retired groups keep their frozen metrics but leave the rating.
        filters.append(ModelGroup.retired_at.is_(None))
        if not include_fallback:
            filters.append(GroupMetric.is_fallback.is_(False))
        if not include_descriptors:
            # Descriptions ("Baggy", "Wide Leg") are no models: hidden unless asked for.
            filters.append(~ModelGroup.slug.startswith(DESCRIPTOR_PREFIX, autoescape=True))
    ids = [int(value) for value in brand_ids.split(",") if value]
    if ids:
        scope.append(GroupMetric.brand_id.in_(ids))
    if product_type:
        scope.append(GroupMetric.product_type == product_type)
    elif level != "brand":
        filters.append(GroupMetric.product_type != REVIEW_TYPE)
    if section:
        scope.append(GroupMetric.section == section)
    filters.extend(scope)
    if price_min is not None:
        filters.append(price_column >= price_min)
    if price_max is not None:
        filters.append(price_column <= price_max)
    if new_only:
        filters.append(GroupMetric.is_new.is_(True))
    if min_sales:
        filters.append(sold_column >= min_sales)
    statement = (
        select(GroupMetric, Brand.name, ModelGroup)
        .join(Brand, Brand.id == GroupMetric.brand_id)
        .outerjoin(ModelGroup, ModelGroup.id == GroupMetric.group_id)
        .where(*filters)
    )
    term = search.strip().casefold()
    if term:
        statement = statement.where(
            or_(
                func.lower(Brand.name).contains(term, autoescape=True),
                func.lower(ModelGroup.name).contains(term, autoescape=True),
            )
        )
    total = int(
        await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    )
    rows = list(
        (
            await session.execute(
                statement.order_by(*_order(sort, desc, sold_column, price_column))
                .limit(limit)
                .offset(offset)
            )
        ).tuples()
    )
    versions = await _version_counts(session, [group.id for _, _, group in rows if group])
    computed_at = await session.scalar(select(func.max(GroupMetric.computed_at)))
    return TrendListResponse(
        data=[
            _row(metric, brand, group, window, versions.get(group.id, 0) if group else 0)
            for metric, brand, group in rows
        ],
        total=total,
        computed_at=computed_at,
        no_model=await _no_model_share(session, scope, product_type, sold_column),
    )


@router.get("/groups/{group_id}", response_model=TrendCard)
async def trend_card(
    group_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    window: Annotated[int, Query()] = 30,
) -> TrendCard:
    _check_window(window)
    group = await session.get(ModelGroup, group_id)
    if group is None:
        raise ApiError(404, "group_not_found", "Model group does not exist")
    detail = await group_detail_data(session, group)
    brand = detail.brand
    metric = await session.scalar(
        select(GroupMetric).where(GroupMetric.scope_key == f"model:{group_id}")
    )
    type_metric = await session.scalar(
        select(GroupMetric).where(
            GroupMetric.scope_key == f"type:{group.brand_id}:{group.product_type}"
        )
    )
    version_ids = [item.id for item in detail.versions]
    version_metrics = (
        list(
            await session.scalars(
                select(GroupMetric).where(
                    GroupMetric.scope_key.in_([f"model:{item}" for item in version_ids])
                )
            )
        )
        if version_ids
        else []
    )
    version_groups = {item.id: item for item in detail.versions}
    groups_by_id: dict[int, tuple[str, GroupKind]] = {
        item.id: (item.name, item.kind) for item in (detail, *detail.versions)
    }
    scope_ids = [group.id, *version_ids]
    recent = await _listings(session, scope_ids, groups_by_id, sold=True, limit=25)
    active = await _listings(session, scope_ids, groups_by_id, sold=False, limit=12)
    return TrendCard(
        group=detail,
        metrics=_row(metric, brand, group, window, len(version_ids)) if metric else None,
        computed_at=metric.computed_at if metric else None,
        weekly_median_price=[
            decimal_to_cents(Decimal(value)) if value is not None else None
            for value in (metric.weekly_median_price if metric else [])
        ],
        colors=[VariantRow.model_validate(item) for item in (metric.colors if metric else [])],
        sizes=[VariantRow.model_validate(item) for item in (metric.sizes if metric else [])],
        versions=sorted(
            (
                _row(
                    item,
                    brand,
                    None,
                    window,
                    0,
                    name=version_groups[item.group_id].name,
                    kind=version_groups[item.group_id].kind,
                )
                for item in version_metrics
                if item.group_id in version_groups
            ),
            key=lambda row: (-row.sold_90d, row.name or ""),
        ),
        type_metrics=_row(type_metric, brand, None, window, 0) if type_metric else None,
        recent_sales=recent,
        active_listings=active,
    )


def _check_window(window: int) -> None:
    if window not in WINDOWS:
        raise ApiError(422, "invalid_window", "window must be 7, 30 or 90 days")


def _sold_column(window: int) -> Any:
    return {7: GroupMetric.sold_7d, 30: GroupMetric.sold_30d, 90: GroupMetric.sold_90d}[window]


def _price_column(window: int) -> Any:
    return {
        7: GroupMetric.median_price_7d,
        30: GroupMetric.median_price_30d,
        90: GroupMetric.median_price_90d,
    }[window]


def _order(sort: str, desc: bool, sold: Any, price: Any) -> list[Any]:
    columns: dict[str, Any] = {
        "trend": GroupMetric.trend_score,
        "growth": GroupMetric.growth,
        "sales": sold,
        "price": price,
        "supply": GroupMetric.active_now,
        "new": GroupMetric.first_seen_at,
    }
    if sort == "speed":
        # "Fastest first" means the shortest median time to sell.
        primary = GroupMetric.median_days_to_sell
        ordered = primary.asc() if desc else primary.desc()
    else:
        primary = columns[sort]
        ordered = primary.desc() if desc else primary.asc()
    return [ordered.nulls_last(), GroupMetric.sold_30d.desc(), GroupMetric.id]


def _row(
    metric: GroupMetric,
    brand: str,
    group: ModelGroup | None,
    window: int,
    versions: int,
    *,
    name: str | None = None,
    kind: GroupKind | None = None,
) -> TrendRow:
    sold = {7: metric.sold_7d, 30: metric.sold_30d, 90: metric.sold_90d}[window]
    price = {
        7: metric.median_price_7d or metric.median_price_30d,
        30: metric.median_price_30d,
        90: metric.median_price_90d,
    }[window]
    if metric.scope == "brand":
        label: str | None = brand
    elif group is not None:
        label = group.name
    else:
        label = name
    return TrendRow(
        scope=metric.scope,  # type: ignore[arg-type]
        scope_key=metric.scope_key,
        group_id=metric.group_id,
        brand_id=metric.brand_id,
        brand=brand,
        product_type=metric.product_type,
        section=metric.section,
        name=label,
        status=group.status if group is not None else None,
        is_fallback=metric.is_fallback,
        kind=group_kind(group.slug, group.product_type) if group is not None else kind,
        versions=versions,
        listings=metric.listings,
        sold=sold,
        sold_7d=metric.sold_7d,
        sold_30d=metric.sold_30d,
        sold_prev_30d=metric.sold_prev_30d,
        sold_90d=metric.sold_90d,
        growth=metric.growth,
        speed=metric.speed,
        trend_score=metric.trend_score,
        median_days_to_sell=metric.median_days_to_sell,
        sell_through_30d=metric.sell_through_30d,
        median_price=decimal_to_cents(price) if price is not None else None,
        price_change=metric.price_change,
        active_now=metric.active_now,
        new_listings_14d=metric.new_listings_14d,
        is_new=metric.is_new,
        first_seen_at=metric.first_seen_at,
        weekly_sales=list(metric.weekly_sales),
    )


async def _no_model_share(
    session: AsyncSession,
    scope: list[ColumnElement[bool]],
    product_type: str | None,
    sold_column: Any,
) -> NoModelShare:
    """Listings and window sales in "No model" groups against all listings of the scope.

    The totals are the type rows, so a listing is counted once; the review type is left out
    unless it is the requested type, as in the list itself.
    """

    where = list(scope)
    if not product_type:
        where.append(GroupMetric.product_type != REVIEW_TYPE)
    sums = (
        func.coalesce(func.sum(GroupMetric.listings), 0),
        func.coalesce(func.sum(sold_column), 0),
    )
    fallback = (
        await session.execute(
            select(*sums).where(
                GroupMetric.scope == "model", GroupMetric.is_fallback.is_(True), *where
            )
        )
    ).one()
    totals = (await session.execute(select(*sums).where(GroupMetric.scope == "type", *where))).one()
    listings, sold = int(fallback[0]), int(fallback[1])
    total_listings, total_sold = int(totals[0]), int(totals[1])
    return NoModelShare(
        listings=listings,
        total_listings=total_listings,
        sold=sold,
        total_sold=total_sold,
        listings_share=_share(listings, total_listings),
        sold_share=_share(sold, total_sold),
    )


def _share(part: int, total: int) -> Decimal | None:
    if not total:
        return None
    return (Decimal(part) / Decimal(total)).quantize(Decimal("0.0001"))


async def _version_counts(session: AsyncSession, group_ids: list[int]) -> dict[int, int]:
    if not group_ids:
        return {}
    # Only versions that have listings (and therefore metrics) count.
    rows = await session.execute(
        select(ModelGroup.parent_id, func.count())
        .join(GroupMetric, GroupMetric.group_id == ModelGroup.id)
        .where(
            ModelGroup.parent_id.in_(group_ids),
            ModelGroup.status != "ignored",
            ModelGroup.retired_at.is_(None),
        )
        .group_by(ModelGroup.parent_id)
    )
    return {int(parent): int(count) for parent, count in rows if parent is not None}


async def _listings(
    session: AsyncSession,
    group_ids: list[int],
    groups: dict[int, tuple[str, GroupKind]],
    *,
    sold: bool,
    limit: int,
) -> list[SaleRow]:
    order = Listing.sold_at.desc() if sold else Listing.created_at.desc()
    rows = list(
        (
            await session.execute(
                select(Listing, ListingModelAssignment.model_group_id)
                .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
                .where(
                    ListingModelAssignment.model_group_id.in_(group_ids),
                    Listing.status == ("sold" if sold else "active"),
                )
                .order_by(order.nulls_last(), Listing.id.desc())
                .limit(limit)
            )
        ).tuples()
    )
    roots = {
        item.relist_of_id for item, _ in rows if item.relist_of_id is not None
    }
    root_created: dict[int, datetime] = {}
    if roots:
        for root_id, created, first_seen in await session.execute(
            select(Listing.id, Listing.created_at, Listing.first_seen_at).where(
                Listing.id.in_(roots)
            )
        ):
            root_created[root_id] = _aware(created or first_seen)
    result: list[SaleRow] = []
    for item, group_id in rows:
        created = _aware(item.created_at or item.first_seen_at)
        start = root_created.get(item.relist_of_id or -1, created)
        days = (
            max((_aware(item.sold_at) - start).days, 0)
            if sold and item.sold_at is not None and not item.sold_at_is_estimated
            else None
        )
        result.append(
            SaleRow(
                id=item.id,
                grailed_id=item.grailed_id,
                url=item.url,
                title=item.title,
                price=decimal_to_cents(item.sold_price or item.price),
                status=item.status,
                sold_at=item.sold_at,
                created_at=item.created_at,
                days_to_sell=days,
                size=item.size_normalized,
                color=item.color,
                group_id=group_id,
                group_name=groups[group_id][0] if group_id in groups else None,
                kind=groups[group_id][1] if group_id in groups else None,
                relisted=item.relist_of_id is not None,
            )
        )
    return result


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

