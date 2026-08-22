"""Read-only APIs for persisted scoring snapshots and listing analytics."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.db.models import Listing, ListingModelAssignment, ModelGroup
from app.db.session import get_db
from app.domain.listings import decimal_to_cents
from app.services.analytics.service import (
    AnalyticsService,
    BrandAnalyticsData,
    GroupDetailData,
    GroupRowData,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


class GroupRow(BaseModel):
    id: int
    name: str
    brand_name: str
    category: str | None
    available_sizes: list[str]
    available_conditions: list[str]
    sold_count: int
    exact_sold_count: int
    active_count: int
    median_sold_price: int | None
    median_days_to_sell: Decimal | None
    median_sold_likes: Decimal | None
    liquidity_score: Decimal | None
    demand_score: Decimal | None
    price_score: Decimal
    confidence_score: Decimal
    market_opportunity_score: Decimal | None
    scoring_status: str
    model_version: str
    window_days: int
    run_id: int


class GroupListResponse(BaseModel):
    data: list[GroupRow]
    total: int
    limit: int
    offset: int


class ScoreMetrics(BaseModel):
    sold_count: int
    exact_sold_count: int
    active_count: int
    sell_through: Decimal
    median_sold_price: int | None
    median_days_to_sell: Decimal | None
    median_sold_likes: Decimal | None
    liquidity_score: Decimal | None
    demand_score: Decimal | None
    price_score: Decimal
    confidence_score: Decimal
    market_opportunity_score: Decimal | None
    scoring_status: str
    components: dict[str, dict[str, str]]
    confidence_factors: dict[str, Any]
    quality_summary: dict[str, Any]
    warnings: list[str]


class ListingExample(BaseModel):
    id: int
    grailed_id: int
    title: str
    price: int
    likes: int
    sold_at: datetime | None


class GroupDetail(BaseModel):
    id: int
    name: str
    brand: str
    category: str | None
    group_type: str
    model_version: str
    window_days: int
    run_id: int
    input_digest: str
    metrics: ScoreMetrics
    sold_examples: list[ListingExample]
    active_examples: list[ListingExample]


class BrandAnalytics(BaseModel):
    id: int
    name: str
    groups_count: int
    sold_count: int
    active_count: int
    average_liquidity_score: Decimal | None
    average_demand_score: Decimal | None
    average_confidence_score: Decimal
    average_market_opportunity_score: Decimal | None


class BrandListResponse(BaseModel):
    data: list[BrandAnalytics]


class BrandDetailResponse(BaseModel):
    brand: BrandAnalytics
    groups: list[GroupRow]


class ListingAnalytics(BaseModel):
    id: int
    grailed_id: int
    brand_id: int | None
    title: str
    status: str
    category: str | None
    size: str | None
    condition: str | None
    price: int
    sold_price: int | None
    likes_count: int
    days_on_market: int | None
    quality_flags: list[str]
    first_seen_at: datetime
    last_seen_at: datetime


class PriceHistoryRow(BaseModel):
    id: int
    price: int
    observed_at: datetime
    source_run_id: int | None


class PriceHistoryResponse(BaseModel):
    data: list[PriceHistoryRow]


class ListingCatalogRow(BaseModel):
    id: int
    grailed_id: int
    title: str
    brand: str
    status: str
    size: str | None
    color: str | None
    price: int
    created_at: datetime | None
    sold_at: datetime | None
    last_seen_at: datetime
    model_group_id: int | None
    model_name: str | None
    model_sold_count: int
    model_active_count: int


class ListingCatalogResponse(BaseModel):
    data: list[ListingCatalogRow]
    total: int
    limit: int
    offset: int


def _validate_window(window_days: int) -> None:
    if window_days not in {30, 90}:
        raise ApiError(422, "invalid_window", "window_days must be 30 or 90")


@router.get("/listings", response_model=ListingCatalogResponse)
async def listing_catalog(
    session: Annotated[AsyncSession, Depends(get_db)],
    search: str = "",
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListingCatalogResponse:
    filters = []
    if search.strip():
        term = f"%{search.strip()}%"
        filters.append(or_(Listing.title.ilike(term), Listing.brand_name_raw.ilike(term)))
    if status:
        filters.append(Listing.status == status)
    total = int(await session.scalar(select(func.count(Listing.id)).where(*filters)) or 0)
    rows = list(
        await session.execute(
            select(Listing, ListingModelAssignment.model_group_id, ModelGroup.name)
            .outerjoin(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .outerjoin(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
            .where(*filters)
            .order_by(Listing.last_seen_at.desc(), Listing.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    group_ids = {group_id for _, group_id, _ in rows if group_id is not None}
    counts: dict[int, dict[str, int]] = {}
    if group_ids:
        for group_id, listing_status, count in await session.execute(
            select(
                ListingModelAssignment.model_group_id,
                Listing.status,
                func.count(Listing.id),
            )
            .join(Listing, Listing.id == ListingModelAssignment.listing_id)
            .where(ListingModelAssignment.model_group_id.in_(group_ids))
            .group_by(ListingModelAssignment.model_group_id, Listing.status)
        ):
            counts.setdefault(group_id, {})[listing_status] = int(count)
    return ListingCatalogResponse(
        data=[
            ListingCatalogRow(
                id=item.id,
                grailed_id=item.grailed_id,
                title=item.title,
                brand=item.brand_name_raw,
                status=item.status,
                size=item.size_normalized,
                color=item.color,
                price=decimal_to_cents(item.price),
                created_at=item.created_at,
                sold_at=item.sold_at,
                last_seen_at=item.last_seen_at,
                model_group_id=group_id,
                model_name=model_name,
                model_sold_count=counts.get(group_id, {}).get("sold", 0),
                model_active_count=counts.get(group_id, {}).get("active", 0),
            )
            for item, group_id, model_name in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/dashboard", response_model=GroupListResponse)
@router.get("/model-groups", response_model=GroupListResponse)
async def list_model_groups(
    session: Annotated[AsyncSession, Depends(get_db)],
    window_days: Annotated[int, Query()] = 90,
    run_id: int | None = None,
    brand_id: Annotated[int | None, Query(ge=1)] = None,
    product_type: Literal["footwear", "clothing", "accessories"] | None = None,
    search: Annotated[str, Query(max_length=200)] = "",
    scored_only: bool = False,
    sort_by: Literal[
        "name",
        "sold_count",
        "active_count",
        "median_sold_price",
        "demand_score",
        "liquidity_score",
    ] = "demand_score",
    sort_desc: bool = True,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> GroupListResponse:
    _validate_window(window_days)
    service = AnalyticsService(session)
    selected_run = await service.selected_run(
        window_days,
        run_id,
        brand_id=brand_id,
        product_type=product_type,
    )
    if selected_run is None:
        return GroupListResponse(data=[], total=0, limit=limit, offset=offset)
    total = await service.count_group_rows(
        selected_run,
        window_days,
        brand_id=brand_id,
        product_type=product_type,
        search=search,
        scored_only=scored_only,
    )
    rows = await service.list_group_rows(
        selected_run,
        window_days,
        brand_id=brand_id,
        product_type=product_type,
        search=search,
        scored_only=scored_only,
        sort_by=sort_by,
        sort_desc=sort_desc,
        limit=limit,
        offset=offset,
    )
    return GroupListResponse(
        data=[_group_row(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/model-groups/{group_id}", response_model=GroupDetail)
async def model_group_detail(
    group_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    window_days: Annotated[int, Query()] = 90,
    run_id: int | None = None,
) -> GroupDetail:
    _validate_window(window_days)
    service = AnalyticsService(session)
    selected_run = await service.selected_run(window_days, run_id)
    if selected_run is None:
        raise ApiError(404, "scoring_snapshot_not_found", "Scoring snapshot does not exist")
    detail = await service.get_group_detail(group_id, selected_run, window_days)
    if detail is None:
        raise ApiError(404, "scoring_snapshot_not_found", "Scoring snapshot does not exist")
    return _group_detail(detail)


@router.get("/brands", response_model=BrandListResponse)
async def list_brand_analytics(
    session: Annotated[AsyncSession, Depends(get_db)],
    window_days: Annotated[int, Query()] = 90,
    run_id: int | None = None,
) -> BrandListResponse:
    _validate_window(window_days)
    service = AnalyticsService(session)
    selected_run = await service.selected_run(window_days, run_id)
    if selected_run is None:
        return BrandListResponse(data=[])
    aggregates = await service.list_brand_analytics(selected_run, window_days)
    return BrandListResponse(data=[_brand_analytics(item) for item in aggregates])


@router.get("/brands/{brand_id}", response_model=BrandDetailResponse)
async def brand_analytics_detail(
    brand_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    window_days: Annotated[int, Query()] = 90,
    run_id: int | None = None,
) -> BrandDetailResponse:
    _validate_window(window_days)
    service = AnalyticsService(session)
    selected_run = await service.selected_run(window_days, run_id)
    if selected_run is None:
        raise ApiError(404, "brand_analytics_not_found", "Brand analytics do not exist")
    result = await service.get_brand_detail(brand_id, selected_run, window_days)
    if result is None:
        raise ApiError(404, "brand_analytics_not_found", "Brand analytics do not exist")
    brand, groups = result
    return BrandDetailResponse(
        brand=_brand_analytics(brand),
        groups=[_group_row(row) for row in groups],
    )


@router.get("/listings/{listing_id}", response_model=ListingAnalytics)
async def listing_analytics(
    listing_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ListingAnalytics:
    service = AnalyticsService(session)
    listing = await service.get_listing(listing_id)
    if listing is None:
        raise ApiError(404, "listing_not_found", "Listing does not exist")
    return ListingAnalytics(
        id=listing.id,
        grailed_id=listing.grailed_id,
        brand_id=listing.brand_id,
        title=listing.title,
        status=listing.status,
        category=listing.category,
        size=listing.size_normalized,
        condition=listing.condition,
        price=decimal_to_cents(listing.price),
        sold_price=decimal_to_cents(listing.sold_price) if listing.sold_price is not None else None,
        likes_count=listing.likes_count,
        days_on_market=listing.days_on_market,
        quality_flags=list(listing.quality_flags),
        first_seen_at=listing.first_seen_at,
        last_seen_at=listing.last_seen_at,
    )


@router.get("/listings/{listing_id}/price-history", response_model=PriceHistoryResponse)
async def listing_price_history(
    listing_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PriceHistoryResponse:
    service = AnalyticsService(session)
    rows = await service.list_price_history(listing_id)
    if rows is None:
        raise ApiError(404, "listing_not_found", "Listing does not exist")
    return PriceHistoryResponse(
        data=[
            PriceHistoryRow(
                id=row.id,
                price=decimal_to_cents(row.price),
                observed_at=row.observed_at,
                source_run_id=row.source_run_id,
            )
            for row in rows
        ]
    )


def _group_row(row: GroupRowData) -> GroupRow:
    return GroupRow(
        id=row.id,
        name=row.name,
        brand_name=row.brand_name,
        category=row.category,
        available_sizes=row.available_sizes,
        available_conditions=row.available_conditions,
        sold_count=row.sold_count,
        exact_sold_count=row.exact_sold_count,
        active_count=row.active_count,
        median_sold_price=row.median_sold_price,
        median_days_to_sell=row.median_days_to_sell,
        median_sold_likes=row.median_sold_likes,
        liquidity_score=row.liquidity_score,
        demand_score=row.demand_score,
        price_score=row.price_score,
        confidence_score=row.confidence_score,
        market_opportunity_score=row.market_opportunity_score,
        scoring_status=row.scoring_status,
        model_version=row.model_version,
        window_days=row.window_days,
        run_id=row.run_id,
    )


def _group_detail(detail: GroupDetailData) -> GroupDetail:
    return GroupDetail(
        id=detail.id,
        name=detail.name,
        brand=detail.brand,
        category=detail.category,
        group_type=detail.group_type,
        model_version=detail.model_version,
        window_days=detail.window_days,
        run_id=detail.run_id,
        input_digest=detail.input_digest,
        metrics=ScoreMetrics(
            sold_count=detail.metrics.sold_count,
            exact_sold_count=detail.metrics.exact_sold_count,
            active_count=detail.metrics.active_count,
            sell_through=detail.metrics.sell_through,
            median_sold_price=detail.metrics.median_sold_price,
            median_days_to_sell=detail.metrics.median_days_to_sell,
            median_sold_likes=detail.metrics.median_sold_likes,
            liquidity_score=detail.metrics.liquidity_score,
            demand_score=detail.metrics.demand_score,
            price_score=detail.metrics.price_score,
            confidence_score=detail.metrics.confidence_score,
            market_opportunity_score=detail.metrics.market_opportunity_score,
            scoring_status=detail.metrics.scoring_status,
            components=detail.metrics.components,
            confidence_factors=detail.metrics.confidence_factors,
            quality_summary=detail.metrics.quality_summary,
            warnings=detail.metrics.warnings,
        ),
        sold_examples=[
            ListingExample(
                id=item.id,
                grailed_id=item.grailed_id,
                title=item.title,
                price=item.price,
                likes=item.likes,
                sold_at=item.sold_at,
            )
            for item in detail.sold_examples
        ],
        active_examples=[
            ListingExample(
                id=item.id,
                grailed_id=item.grailed_id,
                title=item.title,
                price=item.price,
                likes=item.likes,
                sold_at=item.sold_at,
            )
            for item in detail.active_examples
        ],
    )


def _brand_analytics(data: BrandAnalyticsData) -> BrandAnalytics:
    return BrandAnalytics(
        id=data.id,
        name=data.name,
        groups_count=data.groups_count,
        sold_count=data.sold_count,
        active_count=data.active_count,
        average_liquidity_score=data.average_liquidity_score,
        average_demand_score=data.average_demand_score,
        average_confidence_score=data.average_confidence_score,
        average_market_opportunity_score=data.average_market_opportunity_score,
    )
