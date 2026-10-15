"""Recompute group metrics for brands in one pass over their listings."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand, GroupMetric, Listing, ListingModelAssignment, ModelGroup
from app.services.grouping.kinds import NONE_SLUG
from app.services.grouping.policy import REVIEW_TYPE, load_policy
from app.services.metrics.calculator import MetricListing, ScopeMetrics, compute_metrics
from app.services.metrics.history import write_month_stats, write_snapshots
from app.services.metrics.supply import dormant_wholesalers, status_at

METRICS_VERSION = "metrics-v2"
_EXCLUDED = {"possible_replica"}
LOT_FLAG = "lot_or_bundle"
_PRICE_EXCLUDED = {"price_outlier", LOT_FLAG}
_INSERT_CHUNK = 500
NEW_CONDITION = "is_new"


@dataclass(slots=True)
class MetricsResult:
    brands: int
    rows: int
    listings: int
    duration_s: float

    def summary(self) -> dict[str, Any]:
        return {
            "version": METRICS_VERSION,
            "brands": self.brands,
            "rows": self.rows,
            "listings": self.listings,
            "duration_s": round(self.duration_s, 2),
        }


@dataclass(slots=True)
class BrandScopes:
    groups: dict[int, ModelGroup]
    by_scope: defaultdict[str, list[MetricListing]]
    scope_meta: dict[str, dict[str, Any]]
    listings: int


@dataclass(frozen=True, slots=True)
class _Row:
    """One selected listing; dates are UTC-aware and ``listed_at`` falls back to first seen."""

    id: int
    status: str
    price: Decimal
    sold_price: Decimal | None
    created_at: datetime | None
    first_seen_at: datetime
    sold_at: datetime | None
    sold_estimated: bool
    flags: list[str] | None
    color: str | None
    size: str | None
    relist_of: int | None
    product_type: str | None
    group_id: int | None
    condition: str | None
    seller: str | None
    bumped_at: datetime | None
    price_initial: Decimal | None
    price_updated_at: datetime | None
    department: str | None
    removed_checked_at: datetime | None

    def __post_init__(self) -> None:
        for name in (
            "created_at",
            "first_seen_at",
            "sold_at",
            "bumped_at",
            "price_updated_at",
            "removed_checked_at",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _aware(value))

    @property
    def listed_at(self) -> datetime:
        return self.created_at or self.first_seen_at


def _state_at(row: _Row, moment: datetime) -> _Row | None:
    status = status_at(row.status, row.listed_at, row.sold_at, row.removed_checked_at, moment)
    if status is None:
        return None
    return replace(
        row,
        status=status,
        sold_at=row.sold_at if status == "sold" else None,
        bumped_at=row.bumped_at if row.bumped_at and row.bumped_at <= moment else None,
        price_updated_at=row.price_updated_at
        if row.price_updated_at and row.price_updated_at <= moment
        else None,
    )


class MetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._sections = {
            type_id: item.section for type_id, item in load_policy().taxonomy.types.items()
        }

    async def recompute(
        self, brand_ids: Iterable[int] | None = None, *, as_of: datetime | None = None
    ) -> MetricsResult:
        started = time.perf_counter()
        moment = as_of or datetime.now(UTC)
        if brand_ids is None:
            selected = list(await self._session.scalars(select(Brand.id).order_by(Brand.id)))
        else:
            selected = sorted(set(brand_ids))
        rows = listings = 0
        for brand_id in selected:
            brand_rows, brand_listings = await self._brand(brand_id, moment)
            rows += brand_rows
            listings += brand_listings
        await self._session.flush()
        return MetricsResult(len(selected), rows, listings, time.perf_counter() - started)

    async def scopes(
        self, brand_id: int, as_of: datetime, *, historical: bool = False
    ) -> BrandScopes:
        """The brand's listings by scope; ``historical`` rebuilds their state at ``as_of``.

        A historical state drops listings created later and reopens sales and removals seen
        after ``as_of`` (``supply.status_at``); bumps and price changes after it are ignored.
        """

        groups = {
            group.id: group
            for group in await self._session.scalars(
                select(ModelGroup).where(ModelGroup.brand_id == brand_id)
            )
        }
        statement = (
            select(
                Listing.id,
                Listing.status,
                Listing.price,
                Listing.sold_price,
                Listing.created_at,
                Listing.first_seen_at,
                Listing.sold_at,
                Listing.sold_at_is_estimated,
                Listing.quality_flags,
                Listing.color,
                Listing.size_normalized,
                Listing.relist_of_id,
                Listing.product_type,
                ListingModelAssignment.model_group_id,
                Listing.condition,
                Listing.seller_identity,
                Listing.bumped_at,
                Listing.price_initial,
                Listing.price_updated_at,
                Listing.department,
                Listing.removed_checked_at,
            )
            .outerjoin(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .where(Listing.brand_id == brand_id)
        )
        raw = [_Row(*row) for row in (await self._session.execute(statement)).tuples().all()]
        if historical:
            raw = [state for row in raw if (state := _state_at(row, as_of)) is not None]
        created = {row.id: row.listed_at for row in raw}
        wholesalers = dormant_wholesalers(
            ((row.seller, row.status, row.sold_at) for row in raw), as_of
        )
        result = BrandScopes(groups, defaultdict(list), {}, len(raw))
        for row in raw:
            flags = set(row.flags or ())
            if flags & _EXCLUDED:
                continue
            item = MetricListing(
                id=row.id,
                status=row.status,
                price=row.price,
                sold_price=row.sold_price,
                item_created_at=created.get(row.relist_of, row.listed_at)
                if row.relist_of
                else row.listed_at,
                listed_at=row.listed_at,
                sold_at=row.sold_at,
                exact_sale=not row.sold_estimated,
                price_usable=not flags & _PRICE_EXCLUDED,
                is_relist=row.relist_of is not None,
                color=row.color,
                size=row.size,
                new_condition=row.condition == NEW_CONDITION,
                root_id=row.relist_of,
                is_lot=LOT_FLAG in flags,
                wholesale=row.seller in wholesalers,
                fresh_at=max(row.listed_at, row.bumped_at) if row.bumped_at else row.listed_at,
                price_initial=row.price_initial,
                price_updated_at=row.price_updated_at,
                department=row.department,
            )
            self._place(result, row, item, brand_id)
        return result

    def _place(self, result: BrandScopes, row: _Row, item: MetricListing, brand_id: int) -> None:
        by_scope, scope_meta, groups = result.by_scope, result.scope_meta, result.groups
        group = groups.get(row.group_id) if row.group_id is not None else None
        product_type = row.product_type or (group.product_type if group else None)
        if group is not None:
            self._add(by_scope, scope_meta, f"model:{group.id}", item, group, brand_id)
            if group.parent_id is not None and group.parent_id in groups:
                parent = groups[group.parent_id]
                self._add(by_scope, scope_meta, f"model:{parent.id}", item, parent, brand_id)
        if product_type:
            key = f"type:{brand_id}:{product_type}"
            by_scope[key].append(item)
            scope_meta.setdefault(
                key,
                {"scope": "type", "brand_id": brand_id, "product_type": product_type},
            )
        if product_type != REVIEW_TYPE:
            key = f"brand:{brand_id}"
            by_scope[key].append(item)
            scope_meta.setdefault(key, {"scope": "brand", "brand_id": brand_id})

    async def _brand(self, brand_id: int, as_of: datetime) -> tuple[int, int]:
        scopes = await self.scopes(brand_id, as_of)
        groups, by_scope, scope_meta = scopes.groups, scopes.by_scope, scopes.scope_meta
        now = datetime.now(UTC)
        ranked = await self._ranking(brand_id)
        # Retired groups have no listings now; their last metrics stay frozen (grouping-v7).
        # A stale assignment to one must not insert a second row over the frozen one.
        retired = [group.id for group in groups.values() if group.retired_at is not None]
        frozen = {f"model:{group_id}" for group_id in retired}
        values = [
            {
                **self._row(key, scope_meta[key], compute_metrics(items, as_of), now),
                **ranked.get(key, {}),
            }
            for key, items in by_scope.items()
            if key not in frozen
        ]
        await self._session.execute(
            delete(GroupMetric).where(
                GroupMetric.brand_id == brand_id,
                or_(GroupMetric.group_id.is_(None), GroupMetric.group_id.not_in(retired)),
            )
        )
        for start in range(0, len(values), _INSERT_CHUNK):
            await self._session.execute(insert(GroupMetric), values[start : start + _INSERT_CHUNK])
        await write_snapshots(self._session, brand_id, values, as_of.astimezone(UTC).date())
        await write_month_stats(self._session, brand_id, by_scope, scope_meta, as_of, now)
        return len(values), scopes.listings

    async def _ranking(self, brand_id: int) -> dict[str, dict[str, Any]]:
        """Last cross-brand values per scope, kept until the next cross-brand pass.

        The reference scope also feeds the score's hysteresis (v2 plan 2.4).
        """

        rows = await self._session.execute(
            select(
                GroupMetric.scope_key,
                GroupMetric.p30,
                GroupMetric.liquidity_score,
                GroupMetric.ls_ref_scope,
                GroupMetric.badges,
            ).where(GroupMetric.brand_id == brand_id)
        )
        return {
            key: {
                "p30": p30,
                "liquidity_score": score,
                "ls_ref_scope": reference,
                "badges": badges,
            }
            for key, p30, score, reference, badges in rows.tuples().all()
        }

    @staticmethod
    def _add(
        by_scope: dict[str, list[MetricListing]],
        scope_meta: dict[str, dict[str, Any]],
        key: str,
        item: MetricListing,
        group: ModelGroup,
        brand_id: int,
    ) -> None:
        by_scope[key].append(item)
        scope_meta.setdefault(
            key,
            {
                "scope": "model",
                "brand_id": brand_id,
                "product_type": group.product_type,
                "group_id": group.id,
                "is_line": group.parent_id is None,
                "is_fallback": group.slug == NONE_SLUG,
            },
        )

    def _row(
        self, key: str, meta: dict[str, Any], metrics: ScopeMetrics, now: datetime
    ) -> dict[str, Any]:
        product_type = meta.get("product_type")
        return {
            "scope_key": key,
            "scope": meta["scope"],
            "brand_id": meta["brand_id"],
            "product_type": product_type,
            "section": self._sections.get(product_type) if product_type else None,
            "group_id": meta.get("group_id"),
            "is_line": meta.get("is_line", False),
            "is_fallback": meta.get("is_fallback", False),
            "listings": metrics.listings,
            "sold_7d": metrics.sold_7d,
            "sold_30d": metrics.sold_30d,
            "sold_prev_30d": metrics.sold_prev_30d,
            "sold_90d": metrics.sold_90d,
            "weekly_sales": metrics.weekly_sales,
            "weekly_median_price": [
                str(value) if value is not None else None for value in metrics.weekly_median_price
            ],
            "median_price_7d": metrics.median_price_7d,
            "median_price_30d": metrics.median_price_30d,
            "median_price_90d": metrics.median_price_90d,
            "price_change": metrics.price_change,
            "median_days_to_sell": metrics.median_days_to_sell,
            "active_now": metrics.active_now,
            "new_listings_14d": metrics.new_listings_14d,
            "sell_through_30d": metrics.sell_through_30d,
            "growth": metrics.growth,
            "speed": metrics.speed,
            "trend_score": metrics.trend_score,
            "first_seen_at": metrics.first_seen_at,
            "is_new": metrics.is_new,
            "colors": metrics.colors,
            "sizes": metrics.sizes,
            "computed_at": now,
            "active_effective": metrics.active_effective,
            "active_fresh": metrics.active_fresh,
            "p30_sold": metrics.p30_sold,
            "p30_cohort": metrics.p30_cohort,
            "gmv_90d": metrics.gmv_90d,
            "pricing": metrics.pricing,
            "department": metrics.department,
            "p30": None,
            "liquidity_score": None,
            "ls_ref_scope": None,
            "badges": None,
        }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
