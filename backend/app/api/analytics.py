"""Read-only APIs for persisted scoring snapshots and listing analytics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import ApiError
from app.db.models import (
    Listing,
    ListingPriceHistory,
    ModelGroup,
    ModelRule,
    ParserRun,
    ScoringSnapshot,
)
from app.db.session import get_db
from app.services.scoring.service import MODEL_VERSION, rule_matches

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


@router.get("/dashboard", response_model=GroupListResponse)
@router.get("/model-groups", response_model=GroupListResponse)
async def list_model_groups(
    session: Annotated[AsyncSession, Depends(get_db)],
    window_days: Annotated[int, Query()] = 90,
    run_id: int | None = None,
) -> GroupListResponse:
    _validate_window(window_days)
    selected_run = await _selected_run(session, window_days, run_id)
    if selected_run is None:
        return GroupListResponse(data=[])
    rows = await _group_rows(session, selected_run, window_days)
    return GroupListResponse(data=rows)


@router.get("/model-groups/{group_id}", response_model=GroupDetail)
async def model_group_detail(
    group_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    window_days: Annotated[int, Query()] = 90,
    run_id: int | None = None,
) -> GroupDetail:
    _validate_window(window_days)
    selected_run = await _selected_run(session, window_days, run_id)
    if selected_run is None:
        raise ApiError(404, "scoring_snapshot_not_found", "Scoring snapshot does not exist")
    snapshot = await session.scalar(
        select(ScoringSnapshot)
        .where(
            ScoringSnapshot.parser_run_id == selected_run,
            ScoringSnapshot.model_group_id == group_id,
            ScoringSnapshot.window_days == window_days,
            ScoringSnapshot.model_version == MODEL_VERSION,
        )
        .options(
            selectinload(ScoringSnapshot.model_group).selectinload(ModelGroup.brand),
            selectinload(ScoringSnapshot.model_group).selectinload(ModelGroup.rule),
        )
    )
    if snapshot is None:
        raise ApiError(404, "scoring_snapshot_not_found", "Scoring snapshot does not exist")
    examples = await _group_listings(session, snapshot.model_group)
    sold = [item for item in examples if item.status == "sold"][:20]
    active = [item for item in examples if item.status == "active"][:20]
    group = snapshot.model_group
    return GroupDetail(
        id=group.id,
        name=group.name,
        brand=group.brand.name,
        category=group.category,
        group_type=group.group_type,
        model_version=snapshot.model_version,
        window_days=snapshot.window_days,
        run_id=snapshot.parser_run_id,
        input_digest=snapshot.input_digest,
        metrics=_metrics(snapshot),
        sold_examples=[_example(item) for item in sold],
        active_examples=[_example(item) for item in active],
    )


@router.get("/brands", response_model=BrandListResponse)
async def list_brand_analytics(
    session: Annotated[AsyncSession, Depends(get_db)],
    window_days: Annotated[int, Query()] = 90,
    run_id: int | None = None,
) -> BrandListResponse:
    _validate_window(window_days)
    selected_run = await _selected_run(session, window_days, run_id)
    if selected_run is None:
        return BrandListResponse(data=[])
    snapshots = await _snapshots(session, selected_run, window_days)
    return BrandListResponse(data=_brand_aggregates(snapshots))


@router.get("/brands/{brand_id}", response_model=BrandDetailResponse)
async def brand_analytics_detail(
    brand_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    window_days: Annotated[int, Query()] = 90,
    run_id: int | None = None,
) -> BrandDetailResponse:
    _validate_window(window_days)
    selected_run = await _selected_run(session, window_days, run_id)
    if selected_run is None:
        raise ApiError(404, "brand_analytics_not_found", "Brand analytics do not exist")
    snapshots = [
        item
        for item in await _snapshots(session, selected_run, window_days)
        if item.brand_id == brand_id
    ]
    if not snapshots:
        raise ApiError(404, "brand_analytics_not_found", "Brand analytics do not exist")
    rows = await _group_rows(session, selected_run, window_days, brand_id=brand_id)
    return BrandDetailResponse(brand=_brand_aggregates(snapshots)[0], groups=rows)


@router.get("/listings/{listing_id}", response_model=ListingAnalytics)
async def listing_analytics(
    listing_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ListingAnalytics:
    listing = await session.get(Listing, listing_id)
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
        price=_cents(listing.price),
        sold_price=_cents(listing.sold_price) if listing.sold_price is not None else None,
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
    if await session.get(Listing, listing_id) is None:
        raise ApiError(404, "listing_not_found", "Listing does not exist")
    rows = list(
        await session.scalars(
            select(ListingPriceHistory)
            .where(ListingPriceHistory.listing_id == listing_id)
            .order_by(ListingPriceHistory.observed_at)
        )
    )
    return PriceHistoryResponse(
        data=[
            PriceHistoryRow(
                id=row.id,
                price=_cents(row.price),
                observed_at=row.observed_at,
                source_run_id=row.source_run_id,
            )
            for row in rows
        ]
    )


async def _selected_run(session: AsyncSession, window_days: int, run_id: int | None) -> int | None:
    statement = (
        select(func.max(ScoringSnapshot.parser_run_id))
        .join(ParserRun, ParserRun.id == ScoringSnapshot.parser_run_id)
        .where(
            ScoringSnapshot.window_days == window_days,
            ScoringSnapshot.model_version == MODEL_VERSION,
        )
    )
    if run_id is not None:
        statement = statement.where(ScoringSnapshot.parser_run_id == run_id)
    else:
        statement = statement.where(ParserRun.status.in_(("completed", "partial")))
    value = await session.scalar(statement)
    return int(value) if value is not None else None


async def _snapshots(session: AsyncSession, run_id: int, window_days: int) -> list[ScoringSnapshot]:
    return list(
        await session.scalars(
            select(ScoringSnapshot)
            .where(
                ScoringSnapshot.parser_run_id == run_id,
                ScoringSnapshot.window_days == window_days,
                ScoringSnapshot.model_version == MODEL_VERSION,
            )
            .options(selectinload(ScoringSnapshot.model_group).selectinload(ModelGroup.brand))
            .order_by(ScoringSnapshot.market_opportunity_score.desc(), ScoringSnapshot.id)
        )
    )


async def _group_rows(
    session: AsyncSession,
    run_id: int,
    window_days: int,
    *,
    brand_id: int | None = None,
) -> list[GroupRow]:
    snapshots = await _snapshots(session, run_id, window_days)
    if brand_id is not None:
        snapshots = [item for item in snapshots if item.brand_id == brand_id]
    rows: list[GroupRow] = []
    for snapshot in snapshots:
        group = snapshot.model_group
        listings = await _group_listings(session, group)
        rows.append(
            GroupRow(
                id=group.id,
                name=group.name,
                brand_name=group.brand.name,
                category=group.category,
                available_sizes=sorted(
                    {item.size_normalized for item in listings if item.size_normalized}
                ),
                available_conditions=sorted(
                    {item.condition for item in listings if item.condition}
                ),
                sold_count=snapshot.sold_count,
                active_count=snapshot.active_count,
                median_sold_price=(
                    _cents(snapshot.median_sold_price)
                    if snapshot.median_sold_price is not None
                    else None
                ),
                median_sold_likes_per_day=snapshot.median_sold_likes_per_day,
                liquidity_score=snapshot.liquidity_score,
                price_score=snapshot.price_score,
                confidence_score=snapshot.confidence_score,
                market_opportunity_score=snapshot.market_opportunity_score,
                model_version=snapshot.model_version,
                window_days=snapshot.window_days,
                run_id=snapshot.parser_run_id,
            )
        )
    return rows


async def _group_listings(session: AsyncSession, group: ModelGroup) -> list[Listing]:
    listings = list(
        await session.scalars(
            select(Listing)
            .where(Listing.brand_id == group.brand_id)
            .order_by(Listing.sold_at.desc(), Listing.id)
        )
    )
    rules = list(
        await session.scalars(
            select(ModelRule)
            .where(ModelRule.brand_id == group.brand_id, ModelRule.is_active.is_(True))
            .order_by(ModelRule.id)
        )
    )
    selected: list[Listing] = []
    for listing in listings:
        matching = [rule for rule in rules if rule_matches(rule, listing.title, listing.category)]
        winner = (
            min(matching, key=lambda item: (-len(item.include_keywords), item.id))
            if matching
            else None
        )
        if group.group_type == "rule" and winner is not None and winner.group_id == group.id:
            selected.append(listing)
        elif (
            group.group_type == "fallback"
            and winner is None
            and (listing.category or "Uncategorized") == (group.category or "Uncategorized")
        ):
            selected.append(listing)
    return selected


def _metrics(snapshot: ScoringSnapshot) -> ScoreMetrics:
    return ScoreMetrics(
        sold_count=snapshot.sold_count,
        active_count=snapshot.active_count,
        sell_through=snapshot.sell_through,
        median_sold_price=(
            _cents(snapshot.median_sold_price) if snapshot.median_sold_price is not None else None
        ),
        median_days_to_sell=snapshot.median_days_to_sell,
        median_sold_likes_per_day=snapshot.median_sold_likes_per_day,
        liquidity_score=snapshot.liquidity_score,
        price_score=snapshot.price_score,
        confidence_score=snapshot.confidence_score,
        market_opportunity_score=snapshot.market_opportunity_score,
        components=dict(snapshot.component_breakdown),
        confidence_factors=dict(snapshot.confidence_factors),
        quality_summary=dict(snapshot.quality_summary),
        warnings=list(snapshot.warnings),
    )


def _brand_aggregates(snapshots: list[ScoringSnapshot]) -> list[BrandAnalytics]:
    grouped: dict[int, list[ScoringSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        grouped[snapshot.brand_id].append(snapshot)
    result: list[BrandAnalytics] = []
    for items in grouped.values():
        count = Decimal(len(items))
        result.append(
            BrandAnalytics(
                id=items[0].brand_id,
                name=items[0].model_group.brand.name,
                groups_count=len(items),
                sold_count=sum(item.sold_count for item in items),
                active_count=sum(item.active_count for item in items),
                average_liquidity_score=sum((item.liquidity_score for item in items), Decimal(0))
                / count,
                average_confidence_score=sum((item.confidence_score for item in items), Decimal(0))
                / count,
                average_market_opportunity_score=sum(
                    (item.market_opportunity_score for item in items), Decimal(0)
                )
                / count,
            )
        )
    return sorted(result, key=lambda item: item.average_market_opportunity_score, reverse=True)


def _example(listing: Listing) -> ListingExample:
    return ListingExample(
        id=listing.id,
        title=listing.title,
        price=_cents(listing.sold_price or listing.price),
        likes=listing.likes_count,
        sold_at=listing.sold_at,
    )


def _cents(value: Decimal) -> int:
    return int((value * Decimal(100)).quantize(Decimal(1), ROUND_HALF_UP))


def _validate_window(window_days: int) -> None:
    if window_days not in {30, 90}:
        raise ApiError(422, "invalid_window", "window_days must be 30 or 90")
