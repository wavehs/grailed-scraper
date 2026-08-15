"""Analytics query aggregation and score reporting service."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Listing,
    ListingPriceHistory,
    ModelGroup,
    ModelRule,
    ParserRun,
    ScoringSnapshot,
)
from app.domain.listings import decimal_to_cents
from app.services.scoring.service import MODEL_VERSION, rule_matches


@dataclass(frozen=True, slots=True)
class GroupRowData:
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


@dataclass(frozen=True, slots=True)
class ScoreMetricsData:
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


@dataclass(frozen=True, slots=True)
class ListingExampleData:
    id: int
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
    average_liquidity_score: Decimal
    average_confidence_score: Decimal
    average_market_opportunity_score: Decimal


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def selected_run(self, window_days: int, run_id: int | None = None) -> int | None:
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
        value = await self._session.scalar(statement)
        return int(value) if value is not None else None

    async def list_group_rows(
        self,
        run_id: int,
        window_days: int,
        *,
        brand_id: int | None = None,
    ) -> list[GroupRowData]:
        snapshots = await self._snapshots(run_id, window_days)
        if brand_id is not None:
            snapshots = [item for item in snapshots if item.brand_id == brand_id]
        rows: list[GroupRowData] = []
        for snapshot in snapshots:
            group = snapshot.model_group
            listings = await self._group_listings(group)
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
                    active_count=snapshot.active_count,
                    median_sold_price=(
                        decimal_to_cents(snapshot.median_sold_price)
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
        examples = await self._group_listings(snapshot.model_group)
        sold = [item for item in examples if item.status == "sold"][:20]
        active = [item for item in examples if item.status == "active"][:20]
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
        snapshots = [
            item
            for item in await self._snapshots(run_id, window_days)
            if item.brand_id == brand_id
        ]
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

    async def _snapshots(self, run_id: int, window_days: int) -> list[ScoringSnapshot]:
        return list(
            await self._session.scalars(
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

    async def _group_listings(self, group: ModelGroup) -> list[Listing]:
        listings = list(
            await self._session.scalars(
                select(Listing)
                .where(Listing.brand_id == group.brand_id)
                .order_by(Listing.sold_at.desc(), Listing.id)
            )
        )
        rules = list(
            await self._session.scalars(
                select(ModelRule)
                .where(ModelRule.brand_id == group.brand_id, ModelRule.is_active.is_(True))
                .order_by(ModelRule.id)
            )
        )
        selected: list[Listing] = []
        for listing in listings:
            matching = [
                rule
                for rule in rules
                if rule_matches(rule, listing.title, listing.category)
            ]
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

    def _metrics(self, snapshot: ScoringSnapshot) -> ScoreMetricsData:
        return ScoreMetricsData(
            sold_count=snapshot.sold_count,
            active_count=snapshot.active_count,
            sell_through=snapshot.sell_through,
            median_sold_price=(
                decimal_to_cents(snapshot.median_sold_price)
                if snapshot.median_sold_price is not None
                else None
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

    def _brand_aggregates(self, snapshots: list[ScoringSnapshot]) -> list[BrandAnalyticsData]:
        grouped: dict[int, list[ScoringSnapshot]] = defaultdict(list)
        for snapshot in snapshots:
            grouped[snapshot.brand_id].append(snapshot)
        result: list[BrandAnalyticsData] = []
        for items in grouped.values():
            count = Decimal(len(items))
            avg_liquidity = sum((item.liquidity_score for item in items), Decimal(0)) / count
            avg_confidence = sum((item.confidence_score for item in items), Decimal(0)) / count
            avg_opportunity = (
                sum((item.market_opportunity_score for item in items), Decimal(0)) / count
            )
            result.append(
                BrandAnalyticsData(
                    id=items[0].brand_id,
                    name=items[0].model_group.brand.name,
                    groups_count=len(items),
                    sold_count=sum(item.sold_count for item in items),
                    active_count=sum(item.active_count for item in items),
                    average_liquidity_score=avg_liquidity,
                    average_confidence_score=avg_confidence,
                    average_market_opportunity_score=avg_opportunity,
                )
            )
        return sorted(
            result, key=lambda item: item.average_market_opportunity_score, reverse=True
        )

    def _example(self, listing: Listing) -> ListingExampleData:
        return ListingExampleData(
            id=listing.id,
            title=listing.title,
            price=decimal_to_cents(listing.sold_price or listing.price),
            likes=listing.likes_count,
            sold_at=listing.sold_at,
        )
