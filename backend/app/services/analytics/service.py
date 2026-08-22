"""Analytics query aggregation and score reporting service."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Brand,
    Listing,
    ListingModelAssignment,
    ListingPriceHistory,
    ModelGroup,
    ParserRun,
    ScoringSnapshot,
)
from app.domain.listings import decimal_to_cents
from app.services.scoring.service import MODEL_VERSION

PRODUCT_TYPE_CATEGORIES = {
    "footwear": ("footwear", "womens_footwear"),
    "clothing": (
        "tops",
        "outerwear",
        "bottoms",
        "tailoring",
        "womens_tops",
        "womens_outerwear",
        "womens_bottoms",
        "womens_dresses",
    ),
    "accessories": (
        "accessories",
        "womens_accessories",
        "womens_bags_luggage",
        "womens_jewelry",
    ),
}


@dataclass(frozen=True, slots=True)
class GroupRowData:
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


@dataclass(frozen=True, slots=True)
class ScoreMetricsData:
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


@dataclass(frozen=True, slots=True)
class ListingExampleData:
    id: int
    grailed_id: int
    title: str
    price: int
    likes: int
    sold_at: Any | None


@dataclass(frozen=True, slots=True)
class GroupDetailData:
    id: int
    name: str
    brand: str
    category: str | None
    group_type: str
    model_version: str
    window_days: int
    run_id: int
    input_digest: str
    metrics: ScoreMetricsData
    sold_examples: list[ListingExampleData]
    active_examples: list[ListingExampleData]


@dataclass(frozen=True, slots=True)
class BrandAnalyticsData:
    id: int
    name: str
    groups_count: int
    sold_count: int
    active_count: int
    average_liquidity_score: Decimal | None
    average_demand_score: Decimal | None
    average_confidence_score: Decimal
    average_market_opportunity_score: Decimal | None


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def selected_run(
        self,
        window_days: int,
        run_id: int | None = None,
        *,
        brand_id: int | None = None,
        product_type: str | None = None,
    ) -> int | None:
        eligible = (
            select(ScoringSnapshot.parser_run_id)
            .join(ParserRun, ParserRun.id == ScoringSnapshot.parser_run_id)
            .join(ModelGroup, ModelGroup.id == ScoringSnapshot.model_group_id)
            .where(
                ScoringSnapshot.model_version == MODEL_VERSION,
                ScoringSnapshot.window_days.in_((30, 90)),
            )
        )
        if brand_id is not None:
            eligible = eligible.where(ScoringSnapshot.brand_id == brand_id)
        if product_type is not None:
            eligible = eligible.where(_product_type_filter(product_type))
        if run_id is not None:
            eligible = eligible.where(ScoringSnapshot.parser_run_id == run_id)
        else:
            eligible = eligible.where(ParserRun.status.in_(("completed", "partial")))
        eligible = eligible.group_by(ScoringSnapshot.parser_run_id).having(
            func.count(func.distinct(ScoringSnapshot.window_days)) == 2
        )
        candidates = eligible.subquery()
        value = await self._session.scalar(select(func.max(candidates.c.parser_run_id)))
        return int(value) if value is not None else None

    async def list_group_rows(
        self,
        run_id: int,
        window_days: int,
        *,
        brand_id: int | None = None,
        product_type: str | None = None,
        search: str = "",
        scored_only: bool = False,
        sort_by: str = "demand_score",
        sort_desc: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[GroupRowData]:
        snapshots = await self._snapshots(
            run_id,
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
        listings_by_group = await self._listings_by_group(
            [snapshot.model_group for snapshot in snapshots]
        )
        rows: list[GroupRowData] = []
        for snapshot in snapshots:
            group = snapshot.model_group
            listings = listings_by_group[group.id]
            rows.append(
                GroupRowData(
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
                    exact_sold_count=snapshot.exact_sold_count,
                    active_count=snapshot.active_count,
                    median_sold_price=(
                        decimal_to_cents(snapshot.median_sold_price)
                        if snapshot.median_sold_price is not None
                        else None
                    ),
                    median_days_to_sell=snapshot.median_days_to_sell,
                    median_sold_likes=snapshot.median_sold_likes,
                    liquidity_score=snapshot.liquidity_score,
                    demand_score=snapshot.demand_score,
                    price_score=snapshot.price_score,
                    confidence_score=snapshot.confidence_score,
                    market_opportunity_score=snapshot.market_opportunity_score,
                    scoring_status=snapshot.scoring_status,
                    model_version=snapshot.model_version,
                    window_days=snapshot.window_days,
                    run_id=snapshot.parser_run_id,
                )
            )
        return rows

    async def count_group_rows(
        self,
        run_id: int,
        window_days: int,
        *,
        brand_id: int | None = None,
        product_type: str | None = None,
        search: str = "",
        scored_only: bool = False,
    ) -> int:
        statement = (
            select(func.count(ScoringSnapshot.id))
            .join(ModelGroup, ModelGroup.id == ScoringSnapshot.model_group_id)
            .join(Brand, Brand.id == ScoringSnapshot.brand_id)
            .where(
                ScoringSnapshot.parser_run_id == run_id,
                ScoringSnapshot.window_days == window_days,
                ScoringSnapshot.model_version == MODEL_VERSION,
            )
        )
        if brand_id is not None:
            statement = statement.where(ScoringSnapshot.brand_id == brand_id)
        if product_type is not None:
            statement = statement.where(_product_type_filter(product_type))
        if search.strip():
            statement = statement.where(_search_filter(search))
        if scored_only:
            statement = statement.where(ScoringSnapshot.scoring_status == "scored")
        return int(await self._session.scalar(statement) or 0)

    async def get_group_detail(
        self,
        group_id: int,
        run_id: int,
        window_days: int,
    ) -> GroupDetailData | None:
        snapshot = await self._session.scalar(
            select(ScoringSnapshot)
            .where(
                ScoringSnapshot.parser_run_id == run_id,
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
            return None
        examples = (await self._listings_by_group([snapshot.model_group]))[group_id]
        sold, active = _snapshot_examples(examples, snapshot.as_of, snapshot.window_days)
        group = snapshot.model_group
        return GroupDetailData(
            id=group.id,
            name=group.name,
            brand=group.brand.name,
            category=group.category,
            group_type=group.group_type,
            model_version=snapshot.model_version,
            window_days=snapshot.window_days,
            run_id=snapshot.parser_run_id,
            input_digest=snapshot.input_digest,
            metrics=self._metrics(snapshot),
            sold_examples=[self._example(item) for item in sold],
            active_examples=[self._example(item) for item in active],
        )

    async def list_brand_analytics(self, run_id: int, window_days: int) -> list[BrandAnalyticsData]:
        snapshots = await self._snapshots(run_id, window_days)
        return self._brand_aggregates(snapshots)

    async def get_brand_detail(
        self,
        brand_id: int,
        run_id: int,
        window_days: int,
    ) -> tuple[BrandAnalyticsData, list[GroupRowData]] | None:
        snapshots = await self._snapshots(run_id, window_days, brand_id=brand_id)
        if not snapshots:
            return None
        rows = await self.list_group_rows(run_id, window_days, brand_id=brand_id)
        return self._brand_aggregates(snapshots)[0], rows

    async def get_listing(self, listing_id: int) -> Listing | None:
        return await self._session.get(Listing, listing_id)

    async def list_price_history(self, listing_id: int) -> list[ListingPriceHistory] | None:
        if await self.get_listing(listing_id) is None:
            return None
        return list(
            await self._session.scalars(
                select(ListingPriceHistory)
                .where(ListingPriceHistory.listing_id == listing_id)
                .order_by(ListingPriceHistory.observed_at)
            )
        )

    async def _snapshots(
        self,
        run_id: int,
        window_days: int,
        *,
        brand_id: int | None = None,
        product_type: str | None = None,
        search: str = "",
        scored_only: bool = False,
        sort_by: str = "demand_score",
        sort_desc: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ScoringSnapshot]:
        sort_column = {
            "name": ModelGroup.name,
            "sold_count": ScoringSnapshot.sold_count,
            "active_count": ScoringSnapshot.active_count,
            "median_sold_price": ScoringSnapshot.median_sold_price,
            "demand_score": ScoringSnapshot.demand_score,
            "liquidity_score": ScoringSnapshot.liquidity_score,
        }.get(sort_by, ScoringSnapshot.demand_score)
        statement = (
            select(ScoringSnapshot)
            .join(ModelGroup, ModelGroup.id == ScoringSnapshot.model_group_id)
            .join(Brand, Brand.id == ScoringSnapshot.brand_id)
            .where(
                ScoringSnapshot.parser_run_id == run_id,
                ScoringSnapshot.window_days == window_days,
                ScoringSnapshot.model_version == MODEL_VERSION,
            )
            .options(selectinload(ScoringSnapshot.model_group).selectinload(ModelGroup.brand))
            .order_by(
                sort_column.is_(None),
                sort_column.desc() if sort_desc else sort_column.asc(),
                ScoringSnapshot.id,
            )
        )
        if brand_id is not None:
            statement = statement.where(ScoringSnapshot.brand_id == brand_id)
        if product_type is not None:
            statement = statement.where(_product_type_filter(product_type))
        if search.strip():
            statement = statement.where(_search_filter(search))
        if scored_only:
            statement = statement.where(ScoringSnapshot.scoring_status == "scored")
        if limit is not None:
            statement = statement.limit(limit).offset(offset)
        return list(await self._session.scalars(statement))

    async def _listings_by_group(self, groups: list[ModelGroup]) -> dict[int, list[Listing]]:
        selected: dict[int, list[Listing]] = {group.id: [] for group in groups}
        if not groups:
            return selected
        rows = await self._session.execute(
            select(Listing, ListingModelAssignment.model_group_id)
            .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .where(ListingModelAssignment.model_group_id.in_(selected))
            .order_by(Listing.sold_at.desc(), Listing.id)
        )
        for listing, group_id in rows:
            selected[group_id].append(listing)
        return selected

    def _metrics(self, snapshot: ScoringSnapshot) -> ScoreMetricsData:
        return ScoreMetricsData(
            sold_count=snapshot.sold_count,
            exact_sold_count=snapshot.exact_sold_count,
            active_count=snapshot.active_count,
            sell_through=snapshot.sell_through,
            median_sold_price=(
                decimal_to_cents(snapshot.median_sold_price)
                if snapshot.median_sold_price is not None
                else None
            ),
            median_days_to_sell=snapshot.median_days_to_sell,
            median_sold_likes=snapshot.median_sold_likes,
            liquidity_score=snapshot.liquidity_score,
            demand_score=snapshot.demand_score,
            price_score=snapshot.price_score,
            confidence_score=snapshot.confidence_score,
            market_opportunity_score=snapshot.market_opportunity_score,
            scoring_status=snapshot.scoring_status,
            components=dict(snapshot.component_breakdown),
            confidence_factors=dict(snapshot.confidence_factors),
            quality_summary=dict(snapshot.quality_summary),
            warnings=list(snapshot.warnings),
        )

    def _brand_aggregates(self, snapshots: list[ScoringSnapshot]) -> list[BrandAnalyticsData]:
        grouped: dict[int, list[ScoringSnapshot]] = defaultdict(list)
        for snapshot in snapshots:
            grouped[snapshot.brand_id].append(snapshot)
        result: list[BrandAnalyticsData] = []
        for items in grouped.values():
            count = Decimal(len(items))
            liquidity = [item.liquidity_score for item in items if item.liquidity_score is not None]
            demand = [item.demand_score for item in items if item.demand_score is not None]
            avg_liquidity = _average(liquidity)
            avg_demand = _average(demand)
            avg_confidence = sum((item.confidence_score for item in items), Decimal(0)) / count
            result.append(
                BrandAnalyticsData(
                    id=items[0].brand_id,
                    name=items[0].model_group.brand.name,
                    groups_count=len(items),
                    sold_count=sum(item.sold_count for item in items),
                    active_count=sum(item.active_count for item in items),
                    average_liquidity_score=avg_liquidity,
                    average_demand_score=avg_demand,
                    average_confidence_score=avg_confidence,
                    average_market_opportunity_score=avg_demand,
                )
            )
        return sorted(
            result,
            key=lambda item: item.average_demand_score or Decimal(-1),
            reverse=True,
        )

    def _example(self, listing: Listing) -> ListingExampleData:
        return ListingExampleData(
            id=listing.id,
            grailed_id=listing.grailed_id,
            title=listing.title,
            price=decimal_to_cents(listing.sold_price or listing.price),
            likes=listing.likes_count,
            sold_at=listing.sold_at,
        )


def _search_filter(value: str) -> Any:
    term = value.strip().casefold()
    return or_(
        func.lower(ModelGroup.name).contains(term, autoescape=True),
        func.lower(Brand.name).contains(term, autoescape=True),
        func.lower(func.coalesce(ModelGroup.category, "")).contains(term, autoescape=True),
    )


def _product_type_filter(value: str) -> Any:
    return func.lower(ModelGroup.category).in_(PRODUCT_TYPE_CATEGORIES[value])


def _snapshot_examples(
    listings: list[Listing], as_of: datetime, window_days: int
) -> tuple[list[Listing], list[Listing]]:
    end = _aware(as_of)
    cutoff = end - timedelta(days=window_days)
    sold = [
        item
        for item in listings
        if item.status == "sold"
        and item.sold_at is not None
        and cutoff <= _aware(item.sold_at) <= end
    ][:20]
    active = [
        item
        for item in listings
        if item.status == "active" and _aware(item.created_at or item.first_seen_at) <= end
    ][:20]
    return sold, active


def _average(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
