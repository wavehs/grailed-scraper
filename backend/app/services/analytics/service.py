"""Analytics query aggregation and score reporting service."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

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
from app.services.scoring.calculator import decimal_median
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
    snapshot_id: int
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
    created_at: Any | None = None
    days_on_market: int | None = None


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
    variant_breakdown: dict[str, list[dict[str, Any]]]
    metrics: ScoreMetricsData
    sold_examples: list[ListingExampleData]
    active_examples: list[ListingExampleData]


@dataclass(frozen=True, slots=True)
class BrandAnalyticsData:
    id: int
    name: str
    groups_count: int
    sold_count: int
    exact_sold_count: int
    active_count: int
    median_sold_price: int | None
    median_days_to_sell: Decimal | None
    median_sold_likes: Decimal | None
    sell_through: Decimal | None
    demand_score: Decimal | None
    liquidity_score: Decimal | None
    confidence_score: Decimal
    market_opportunity_score: Decimal | None
    scoring_status: str
    average_liquidity_score: Decimal | None
    average_demand_score: Decimal | None
    average_confidence_score: Decimal
    average_market_opportunity_score: Decimal | None


@dataclass(frozen=True, slots=True)
class SnapshotPosition:
    value: str | int | Decimal | None
    id: int


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
        eligible = select(ParserRun.id)
        if run_id is not None:
            eligible = eligible.where(ParserRun.id == run_id)
        else:
            eligible = eligible.where(ParserRun.status.in_(("completed", "partial")))
        for required_window in (30, 90):
            probe = select(ScoringSnapshot.id).where(
                ScoringSnapshot.parser_run_id == ParserRun.id,
                ScoringSnapshot.model_version == MODEL_VERSION,
                ScoringSnapshot.window_days == required_window,
            )
            if brand_id is not None:
                probe = probe.where(ScoringSnapshot.brand_id == brand_id)
            if product_type is not None:
                probe = probe.join(
                    ModelGroup, ModelGroup.id == ScoringSnapshot.model_group_id
                ).where(_product_type_filter(product_type))
            eligible = eligible.where(probe.exists())
        value = await self._session.scalar(eligible.order_by(ParserRun.id.desc()).limit(1))
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
        position: SnapshotPosition | None = None,
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
            position=position,
        )
        facets_by_group = await self._listing_facets_by_group(
            [snapshot.model_group_id for snapshot in snapshots]
        )
        rows: list[GroupRowData] = []
        for snapshot in snapshots:
            group = snapshot.model_group
            sizes, conditions = facets_by_group[group.id]
            rows.append(
                GroupRowData(
                    snapshot_id=snapshot.id,
                    id=group.id,
                    name=group.name,
                    brand_name=group.brand.name,
                    category=group.category,
                    available_sizes=sorted(sizes),
                    available_conditions=sorted(conditions),
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
            )
        )
        if snapshot is None:
            return None
        sold, active = await self._listing_examples(group_id, snapshot.as_of, snapshot.window_days)
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
            variant_breakdown=snapshot.variant_breakdown,
            metrics=self._metrics(snapshot),
            sold_examples=[self._example(item) for item in sold],
            active_examples=[self._example(item) for item in active],
        )

    async def list_brand_analytics(
        self,
        run_id: int,
        window_days: int,
        *,
        search: str = "",
        product_type: str | None = None,
        scored_only: bool = False,
        sort_by: str = "demand_score",
        sort_desc: bool = True,
        limit: int | None = None,
    ) -> tuple[list[BrandAnalyticsData], int]:
        snapshots = await self._snapshots(
            run_id,
            window_days,
            product_type=product_type,
            search=search,
            scored_only=scored_only,
        )
        aggregates = self._brand_aggregates(snapshots)
        if search.strip():
            term = search.strip().casefold()
            aggregates = [b for b in aggregates if term in b.name.casefold()]
        if scored_only:
            aggregates = [b for b in aggregates if b.scoring_status == "scored"]
        sorted_aggregates = self._sort_brand_aggregates(
            aggregates, sort_by=sort_by, sort_desc=sort_desc
        )
        total = len(sorted_aggregates)
        if limit is not None:
            sorted_aggregates = sorted_aggregates[:limit]
        return sorted_aggregates, total

    def _sort_brand_aggregates(
        self,
        items: list[BrandAnalyticsData],
        sort_by: str = "demand_score",
        sort_desc: bool = True,
    ) -> list[BrandAnalyticsData]:
        def key_fn(item: BrandAnalyticsData) -> Any:
            if sort_by == "name":
                return item.name.casefold()
            if sort_by == "sold_count":
                return item.sold_count
            if sort_by == "active_count":
                return item.active_count
            if sort_by == "median_sold_price":
                return -1 if item.median_sold_price is None else item.median_sold_price
            if sort_by == "median_days_to_sell":
                if item.median_days_to_sell is None:
                    return Decimal("-1") if sort_desc else Decimal("999999")
                return item.median_days_to_sell
            if sort_by == "liquidity_score":
                return Decimal(-1) if item.liquidity_score is None else item.liquidity_score
            return Decimal(-1) if item.demand_score is None else item.demand_score

        return sorted(items, key=key_fn, reverse=sort_desc if sort_by != "name" else not sort_desc)

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
        position: SnapshotPosition | None = None,
    ) -> list[ScoringSnapshot]:
        sort_column = {
            "name": ModelGroup.name,
            "sold_count": ScoringSnapshot.sold_count,
            "active_count": ScoringSnapshot.active_count,
            "median_sold_price": ScoringSnapshot.median_sold_price,
            "demand_score": ScoringSnapshot.demand_score,
            "liquidity_score": ScoringSnapshot.liquidity_score,
        }.get(sort_by, ScoringSnapshot.demand_score)
        statement = select(ScoringSnapshot).where(
            ScoringSnapshot.parser_run_id == run_id,
            ScoringSnapshot.window_days == window_days,
            ScoringSnapshot.model_version == MODEL_VERSION,
        )
        needs_group = product_type is not None or bool(search.strip()) or sort_by == "name"
        if needs_group:
            statement = statement.join(ModelGroup, ModelGroup.id == ScoringSnapshot.model_group_id)
        if search.strip():
            statement = statement.join(Brand, Brand.id == ScoringSnapshot.brand_id)
        statement = statement.options(
            selectinload(ScoringSnapshot.model_group).selectinload(ModelGroup.brand)
        ).order_by(
            sort_column.is_(None),
            sort_column.desc() if sort_desc else sort_column.asc(),
            ScoringSnapshot.id,
        )
        if brand_id is not None:
            statement = statement.where(ScoringSnapshot.brand_id == brand_id)
        if product_type is not None:
            statement = statement.where(_product_type_filter(product_type))
        if search.strip():
            statement = statement.where(_search_filter(search))
        if scored_only:
            statement = statement.where(ScoringSnapshot.scoring_status == "scored")
        if position is not None:
            if position.value is None:
                statement = statement.where(sort_column.is_(None), ScoringSnapshot.id > position.id)
            else:
                range_filter = (
                    sort_column < position.value if sort_desc else sort_column > position.value
                )
                statement = statement.where(
                    or_(
                        sort_column.is_(None),
                        range_filter,
                        and_(
                            sort_column == position.value,
                            ScoringSnapshot.id > position.id,
                        ),
                    )
                )
        if limit is not None:
            statement = statement.limit(limit)
        return list(await self._session.scalars(statement))

    async def _listing_facets_by_group(
        self, group_ids: list[int]
    ) -> dict[int, tuple[set[str], set[str]]]:
        selected: dict[int, tuple[set[str], set[str]]] = {
            group_id: (set(), set()) for group_id in group_ids
        }
        if not group_ids:
            return selected
        rows = await self._session.execute(
            select(
                ListingModelAssignment.model_group_id,
                Listing.size_normalized,
                Listing.condition,
            )
            .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .where(ListingModelAssignment.model_group_id.in_(group_ids))
            .distinct()
        )
        for group_id, size, condition in rows:
            if size:
                selected[group_id][0].add(size)
            if condition:
                selected[group_id][1].add(condition)
        return selected

    async def _listing_examples(
        self, group_id: int, as_of: datetime, window_days: int
    ) -> tuple[list[Listing], list[Listing]]:
        end = _aware(as_of)
        cutoff = end - timedelta(days=window_days)
        columns = (
            Listing.id,
            Listing.grailed_id,
            Listing.title,
            Listing.price,
            Listing.sold_price,
            Listing.likes_count,
            Listing.status,
            Listing.sold_at,
            Listing.created_at,
            Listing.first_seen_at,
            Listing.last_seen_at,
            Listing.days_on_market,
        )
        base = (
            select(Listing)
            .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .where(ListingModelAssignment.model_group_id == group_id)
            .options(load_only(*columns))
        )
        sold = list(
            await self._session.scalars(
                base.where(
                    Listing.status == "sold",
                    Listing.sold_at.is_not(None),
                    Listing.sold_at >= cutoff,
                    Listing.sold_at <= end,
                )
                .order_by(Listing.sold_at.desc(), Listing.id)
                .limit(20)
            )
        )
        active = list(
            await self._session.scalars(
                base.where(
                    Listing.status == "active",
                    func.coalesce(Listing.created_at, Listing.first_seen_at) <= end,
                )
                .order_by(Listing.id)
                .limit(20)
            )
        )
        return sold, active

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
            sold_count = sum(item.sold_count for item in items)
            exact_sold_count = sum(item.exact_sold_count for item in items)
            active_count = sum(item.active_count for item in items)
            prices = [
                item.median_sold_price for item in items if item.median_sold_price is not None
            ]
            median_price = decimal_median(prices)
            median_sold_price = decimal_to_cents(median_price) if median_price is not None else None
            days = [
                item.median_days_to_sell for item in items if item.median_days_to_sell is not None
            ]
            median_days_to_sell = decimal_median(days)
            likes = [item.median_sold_likes for item in items if item.median_sold_likes is not None]
            median_sold_likes = decimal_median(likes)
            sell_through = (
                (Decimal(sold_count) / Decimal(sold_count + active_count)).quantize(
                    Decimal("0.000001")
                )
                if (sold_count + active_count) > 0
                else Decimal(0)
            )
            liquidity = [item.liquidity_score for item in items if item.liquidity_score is not None]
            demand = [item.demand_score for item in items if item.demand_score is not None]
            avg_liquidity = _average(liquidity)
            avg_demand = _average(demand)
            avg_confidence = (
                sum((item.confidence_score for item in items), Decimal(0)) / count
            ).quantize(Decimal("0.01"))
            scoring_status = (
                "scored"
                if avg_demand is not None
                else "insufficient_sales"
                if sold_count < 3
                else "insufficient_temporal_data"
            )
            result.append(
                BrandAnalyticsData(
                    id=items[0].brand_id,
                    name=items[0].model_group.brand.name,
                    groups_count=len(items),
                    sold_count=sold_count,
                    exact_sold_count=exact_sold_count,
                    active_count=active_count,
                    median_sold_price=median_sold_price,
                    median_days_to_sell=median_days_to_sell,
                    median_sold_likes=median_sold_likes,
                    sell_through=sell_through,
                    demand_score=avg_demand,
                    liquidity_score=avg_liquidity,
                    confidence_score=avg_confidence,
                    market_opportunity_score=avg_demand,
                    scoring_status=scoring_status,
                    average_liquidity_score=avg_liquidity,
                    average_demand_score=avg_demand,
                    average_confidence_score=avg_confidence,
                    average_market_opportunity_score=avg_demand,
                )
            )
        return sorted(
            result,
            key=lambda item: item.demand_score or Decimal(-1),
            reverse=True,
        )

    def _example(self, listing: Listing) -> ListingExampleData:
        created = listing.created_at or listing.first_seen_at
        now = datetime.now(UTC)
        if listing.status == "sold":
            if listing.days_on_market is not None:
                days = listing.days_on_market
            elif listing.sold_at and created:
                days = max((_aware(listing.sold_at) - _aware(created)).days, 0)
            else:
                days = None
        elif listing.status == "active":
            days = max((now - _aware(created)).days, 0) if created else None
        else:
            days = listing.days_on_market or (
                max((_aware(listing.last_seen_at) - _aware(created)).days, 0) if created else None
            )

        return ListingExampleData(
            id=listing.id,
            grailed_id=listing.grailed_id,
            title=listing.title,
            price=decimal_to_cents(listing.sold_price or listing.price),
            likes=listing.likes_count,
            sold_at=listing.sold_at,
            created_at=created,
            days_on_market=days,
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


def _average(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
