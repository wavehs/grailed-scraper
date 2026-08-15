"""Read-only APIs for persisted scoring snapshots and listing analytics."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
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
    active_count: int
    median_sold_price: int | None
    median_sold_likes_per_day: Decimal | None
    liquidity_score: Decimal
    price_score: Decimal
    confidence_score: Decimal
    market_opportunity_score: Decimal
    model_version: str
    window_days: int
    run_id: int


class GroupListResponse(BaseModel):
    data: list[GroupRow]


class ScoreMetrics(BaseModel):
    sold_count: int
    active_count: int
    sell_through: Decimal
    median_sold_price: int | None
    median_days_to_sell: Decimal | None
    median_sold_likes_per_day: Decimal | None
    liquidity_score: Decimal
    price_score: Decimal
    confidence_score: Decimal
    market_opportunity_score: Decimal
    components: dict[str, dict[str, str]]
    confidence_factors: dict[str, Any]
    quality_summary: dict[str, Any]
    warnings: list[str]


class ListingExample(BaseModel):
    id: int
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
    average_liquidity_score: Decimal
    average_confidence_score: Decimal
    average_market_opportunity_score: Decimal


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


def _validate_window(window_days: int) -> None:
    if window_days not in {30, 90}:
        raise ApiError(422, "invalid_window", "window_days must be 30 or 90")


@router.get("/dashboard", response_model=GroupListResponse)
@router.get("/model-groups", response_model=GroupListResponse)
async def list_model_groups(
    session: Annotated[AsyncSession, Depends(get_db)],
    window_days: Annotated[int, Query()] = 90,
    run_id: int | None = None,
) -> GroupListResponse:
    _validate_window(window_days)
    service = AnalyticsService(session)
    selected_run = await service.selected_run(window_days, run_id)
    if selected_run is None:
        return GroupListResponse(data=[])
    rows = await service.list_group_rows(selected_run, window_days)
    return GroupListResponse(data=[_group_row(row) for row in rows])


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
        active_count=row.active_count,
        median_sold_price=row.median_sold_price,
        median_sold_likes_per_day=row.median_sold_likes_per_day,
        liquidity_score=row.liquidity_score,
        price_score=row.price_score,
        confidence_score=row.confidence_score,
        market_opportunity_score=row.market_opportunity_score,
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
            active_count=detail.metrics.active_count,
            sell_through=detail.metrics.sell_through,
            median_sold_price=detail.metrics.median_sold_price,
            median_days_to_sell=detail.metrics.median_days_to_sell,
            median_sold_likes_per_day=detail.metrics.median_sold_likes_per_day,
            liquidity_score=detail.metrics.liquidity_score,
            price_score=detail.metrics.price_score,
            confidence_score=detail.metrics.confidence_score,
            market_opportunity_score=detail.metrics.market_opportunity_score,
            components=detail.metrics.components,
            confidence_factors=detail.metrics.confidence_factors,
            quality_summary=detail.metrics.quality_summary,
            warnings=detail.metrics.warnings,
        ),
        sold_examples=[
            ListingExample(
                id=item.id,
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
        average_confidence_score=data.average_confidence_score,
        average_market_opportunity_score=data.average_market_opportunity_score,
    )
