"""Recompute group metrics for brands in one pass over their listings, then rank liquidity."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, delete, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand, GroupMetric, Listing, ListingModelAssignment, ModelGroup
from app.services.grouping.descriptors import DESCRIPTOR_PREFIX
from app.services.grouping.kinds import NONE_SLUG
from app.services.grouping.policy import REVIEW_TYPE, load_policy
from app.services.metrics.calculator import MetricListing, ScopeMetrics, compute_metrics
from app.services.metrics.liquidity import (
    LiquidityCandidate,
    LiquidityPolicy,
    liquidity_scores,
)
from app.services.metrics.liquidity_store import effective_policy

METRICS_VERSION = "metrics-v2"
_SCOPES = ("model", "type", "brand")
_EXCLUDED = {"possible_replica"}
_PRICE_EXCLUDED = {"price_outlier", "lot_or_bundle"}
_INSERT_CHUNK = 500


@dataclass(slots=True)
class MetricsResult:
    brands: int
    rows: int
    listings: int
    duration_s: float
    ranked: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "version": METRICS_VERSION,
            "brands": self.brands,
            "rows": self.rows,
            "listings": self.listings,
            "ranked": self.ranked,
            "duration_s": round(self.duration_s, 2),
        }


class MetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._sections = {
            type_id: item.section for type_id, item in load_policy().taxonomy.types.items()
        }

    async def recompute(
        self,
        brand_ids: Iterable[int] | None = None,
        *,
        as_of: datetime | None = None,
        rank: bool = True,
    ) -> MetricsResult:
        """Recompute the brands, then rank liquidity unless the caller ranks once later."""

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
        # Percentiles need every brand, so the whole table is ranked after any recompute.
        ranked = await self.rank() if rank else 0
        await self._session.flush()
        return MetricsResult(
            len(selected), rows, listings, time.perf_counter() - started, ranked
        )

    async def rank(self) -> int:
        """Rank with the saved policy inside SQLite's write lock.

        Clearing the scores first takes the lock, so the policy and the rows read below
        cannot change (a settings save, a regroup) before the scores are written.
        """

        await self._session.execute(update(GroupMetric).values(liquidity_score=None))
        return await rank_liquidity(self._session, await effective_policy(self._session))

    async def _brand(self, brand_id: int, as_of: datetime) -> tuple[int, int]:
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
                Listing.likes_count,
            )
            .outerjoin(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .where(Listing.brand_id == brand_id)
        )
        raw = list(await self._session.execute(statement))
        created = {row[0]: _aware(row[4] or row[5]) for row in raw}
        by_scope: dict[str, list[MetricListing]] = defaultdict(list)
        scope_meta: dict[str, dict[str, Any]] = {}
        for row in raw:
            flags = set(row[8] or ())
            if flags & _EXCLUDED:
                continue
            listed_at = created[row[0]]
            item = MetricListing(
                id=row[0],
                status=row[1],
                price=row[2],
                sold_price=row[3],
                item_created_at=created.get(row[11], listed_at) if row[11] else listed_at,
                listed_at=listed_at,
                sold_at=_aware(row[6]) if row[6] else None,
                exact_sale=not row[7],
                price_usable=not flags & _PRICE_EXCLUDED,
                is_relist=row[11] is not None,
                color=row[9],
                size=row[10],
                likes=row[14] or 0,
            )
            group = groups.get(row[13]) if row[13] is not None else None
            product_type = row[12] or (group.product_type if group else None)
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
        now = datetime.now(UTC)
        values = [
            self._row(key, scope_meta[key], compute_metrics(items, as_of), now)
            for key, items in by_scope.items()
        ]
        # Retired groups have no listings now; their last metrics stay frozen (grouping-v7).
        retired = [group.id for group in groups.values() if group.retired_at is not None]
        await self._session.execute(
            delete(GroupMetric).where(
                GroupMetric.brand_id == brand_id,
                or_(GroupMetric.group_id.is_(None), GroupMetric.group_id.not_in(retired)),
            )
        )
        for start in range(0, len(values), _INSERT_CHUNK):
            await self._session.execute(insert(GroupMetric), values[start : start + _INSERT_CHUNK])
        return len(values), len(raw)

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
                str(value) if value is not None else None
                for value in metrics.weekly_median_price
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
            "windows": metrics.windows,
            "first_seen_at": metrics.first_seen_at,
            "is_new": metrics.is_new,
            "colors": metrics.colors,
            "sizes": metrics.sizes,
            "computed_at": now,
        }


async def rank_liquidity(session: AsyncSession, policy: LiquidityPolicy) -> int:
    """Copy the policy period into the ``period_*`` columns of every row, then score each
    scope's rows that pass the threshold against each other. Call it holding the write
    lock (``MetricsService.rank``).

    Model rows are lines only, without "No model", descriptions and retired groups, the
    same rows the trends list shows; the review type is never ranked.
    """

    rows = {
        row_id: _candidate(row_id, section, windows, policy.period_days)
        for row_id, section, windows in await session.execute(
            select(GroupMetric.id, GroupMetric.section, GroupMetric.windows)
        )
    }
    scores: dict[int, Decimal] = {}
    for scope in _SCOPES:
        pool = [rows[row_id] for row_id in await session.scalars(_ranked_rows(scope))]
        scores.update(liquidity_scores(pool, policy))
    values = [
        {
            "id": row_id,
            "period_sold": item.sold,
            "period_median_price": item.median_price,
            "period_median_days": item.median_days_to_sell,
            "period_median_likes": item.median_likes,
            "liquidity_score": scores.get(row_id),
        }
        for row_id, item in rows.items()
    ]
    for start in range(0, len(values), _INSERT_CHUNK):
        await session.execute(update(GroupMetric), values[start : start + _INSERT_CHUNK])
    return len(scores)


def _ranked_rows(scope: str) -> Select[tuple[int]]:
    statement = select(GroupMetric.id).where(GroupMetric.scope == scope)
    if scope == "model":
        statement = statement.join(ModelGroup, ModelGroup.id == GroupMetric.group_id).where(
            GroupMetric.is_line.is_(True),
            GroupMetric.is_fallback.is_(False),
            ModelGroup.retired_at.is_(None),
            ~ModelGroup.slug.startswith(DESCRIPTOR_PREFIX, autoescape=True),
        )
    if scope != "brand":
        statement = statement.where(GroupMetric.product_type != REVIEW_TYPE)
    return statement


def _candidate(
    row_id: int, section: str | None, windows: dict[str, Any] | None, period_days: int
) -> LiquidityCandidate:
    window = (windows or {}).get(str(period_days)) or {}
    return LiquidityCandidate(
        key=row_id,
        section=section,
        sold=int(window.get("sold") or 0),
        median_days_to_sell=_number(window.get("days")),
        median_likes=_number(window.get("likes")),
        median_price=_number(window.get("price")),
    )


def _number(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
