"""Database-backed market-v5 scoring with resolved line identity."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

import orjson
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import load_only

from app.db.models import (
    Brand,
    Listing,
    ListingModelAssignment,
    ModelGroup,
    ParserRun,
    ParserRunTask,
    PhysicalItemMember,
    ScoringSnapshot,
)
from app.services.identity.service import IDENTITY_VERSION
from app.services.scoring.calculator import (
    HUNDRED,
    SIX_PLACES,
    TWO_PLACES,
    MetricDraft,
    confidence_score,
    decimal_median,
    engagement_score,
    finalize_brand,
    frequency_score,
    ratio_score,
    sample_sufficiency,
    velocity_score,
)

MODEL_VERSION = "market-v5"
WINDOWS = (30, 90)
_FULL_EXCLUSIONS = {"possible_replica"}
_PRICE_EXCLUSIONS = {"price_outlier", "lot_or_bundle"}


class ScoringService(Protocol):
    async def score_run(
        self, run_id: int, *, brand_ids: set[int] | None = None
    ) -> dict[str, object]: ...


class OpportunityScoringService:
    """Calculate and persist immutable snapshots from the current listing state."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def score_run(
        self, run_id: int, *, brand_ids: set[int] | None = None
    ) -> dict[str, object]:
        async with self._sessions() as session:
            result = await self.score_run_in_session(session, run_id, brand_ids=brand_ids)
            await session.commit()
            return result

    async def score_run_in_session(
        self,
        session: AsyncSession,
        run_id: int,
        *,
        brand_ids: set[int] | None = None,
        replace: bool = False,
    ) -> dict[str, object]:
        """Score through a caller-owned transaction, optionally replacing snapshots."""

        run = await session.get(ParserRun, run_id)
        if run is None:
            raise LookupError(f"Parser run {run_id} does not exist")
        as_of = _aware(run.started_at or run.created_at)
        coverage, truncated = await _coverage(session, run_id, run.coverage_avg)
        if brand_ids is not None:
            coverage = {
                brand_id: coverage.get(brand_id, run.coverage_avg or Decimal(1))
                for brand_id in brand_ids
            }
            truncated &= brand_ids
        degraded = run.degraded_mode
        groups_total = snapshots_total = 0
        cutoff = as_of - timedelta(days=max(WINDOWS))
        if replace and coverage:
            await session.execute(
                delete(ScoringSnapshot).where(
                    ScoringSnapshot.parser_run_id == run_id,
                    ScoringSnapshot.brand_id.in_(coverage),
                    ScoringSnapshot.model_version == MODEL_VERSION,
                )
            )
            await session.flush()
        for brand_id in sorted(coverage):
            brand = await session.get(Brand, brand_id)
            if brand is None:
                raise LookupError(f"Brand {brand_id} does not exist")
            listings = list(
                await session.scalars(
                    select(Listing)
                    .options(
                        load_only(
                            Listing.id,
                            Listing.grailed_id,
                            Listing.brand_id,
                            Listing.title,
                            Listing.category,
                            Listing.size_normalized,
                            Listing.color,
                            Listing.status,
                            Listing.price,
                            Listing.sold_price,
                            Listing.created_at,
                            Listing.first_seen_at,
                            Listing.sold_at,
                            Listing.sold_at_is_estimated,
                            Listing.days_on_market,
                            Listing.likes_count,
                            Listing.quality_flags,
                        )
                    )
                    .where(
                        Listing.brand_id == brand_id,
                        or_(
                            and_(
                                Listing.status == "sold",
                                Listing.sold_at.between(cutoff, as_of),
                            ),
                            and_(
                                Listing.status == "active",
                                func.coalesce(Listing.created_at, Listing.first_seen_at) <= as_of,
                            ),
                        ),
                    )
                )
            )
            listings = await _canonical_relistings(session, listings)
            assignments, groups = await self._resolved_groups(session, listings, brand_id)
            drafts = _build_drafts(
                listings=listings,
                assignments=assignments,
                groups=groups,
                as_of=as_of,
                coverage_by_brand=coverage,
                truncated_brands=truncated,
                degraded=degraded,
            )
            rows = _finalize_rows(run, groups, drafts, as_of)
            await self._persist(session, run_id, brand_id, rows)
            groups_total += len(groups)
            snapshots_total += len(rows)
        return {
            "status": "completed",
            "model_version": MODEL_VERSION,
            "windows": list(WINDOWS),
            "groups": groups_total,
            "snapshots": snapshots_total,
        }

    async def _resolved_groups(
        self,
        session: AsyncSession,
        listings: Sequence[Listing],
        brand_id: int,
    ) -> tuple[dict[int, int], dict[int, ModelGroup]]:
        listing_ids = {listing.id for listing in listings}
        rows = (
            await session.execute(
                select(ListingModelAssignment, ModelGroup)
                .join(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
                .join(Listing, Listing.id == ListingModelAssignment.listing_id)
                .where(Listing.brand_id == brand_id)
            )
        ).tuples()
        persisted = [
            (assignment, group)
            for assignment, group in rows
            if assignment.listing_id in listing_ids
        ]
        assignments = {
            assignment.listing_id: assignment.model_group_id for assignment, _ in persisted
        }
        stale = [
            assignment.listing_id
            for assignment, _ in persisted
            if assignment.algorithm_version != IDENTITY_VERSION
        ]
        missing = sorted(listing_ids - assignments.keys())
        if stale or missing:
            raise RuntimeError(
                "identity_assignment_missing: "
                f"brand={brand_id} missing={missing[:10]} stale={stale[:10]}"
            )
        groups = {group.id: group for _, group in persisted}
        return assignments, groups

    @staticmethod
    async def _persist(
        session: AsyncSession,
        run_id: int,
        brand_id: int,
        rows: Sequence[ScoringSnapshot],
    ) -> None:
        existing = list(
            await session.scalars(
                select(ScoringSnapshot).where(
                    ScoringSnapshot.parser_run_id == run_id,
                    ScoringSnapshot.brand_id == brand_id,
                    ScoringSnapshot.model_version == MODEL_VERSION,
                )
            )
        )
        existing_by_key = {(row.model_group_id, row.window_days): row for row in existing}
        calculated_keys: set[tuple[int, int]] = set()
        for row in rows:
            key = (row.model_group_id, row.window_days)
            calculated_keys.add(key)
            previous = existing_by_key.get(key)
            if previous is not None:
                if previous.input_digest != row.input_digest:
                    raise RuntimeError("scoring_snapshot_conflict")
                continue
            session.add(row)
        if set(existing_by_key) - calculated_keys:
            raise RuntimeError("scoring_snapshot_conflict")
        await session.flush()


class NoOpScoringService:
    """Explicit test seam retained for callers that opt out of stage-nine scoring."""

    async def score_run(
        self, run_id: int, *, brand_ids: set[int] | None = None
    ) -> dict[str, object]:
        return {"run_id": run_id, "status": "skipped", "reason": "scoring_disabled"}


async def _coverage(
    session: AsyncSession, run_id: int, fallback: Decimal | None
) -> tuple[dict[int, Decimal], set[int]]:
    tasks = list(await session.scalars(select(ParserRunTask).where(ParserRunTask.run_id == run_id)))
    values: dict[int, list[Decimal]] = defaultdict(list)
    truncated: set[int] = set()
    for task in tasks:
        if task.brand_id is None:
            continue
        if task.coverage is not None:
            values[task.brand_id].append(task.coverage)
        if task.status == "truncated":
            truncated.add(task.brand_id)
    default = fallback if fallback is not None else Decimal(1)
    result = {
        brand_id: sum(items, Decimal(0)) / Decimal(len(items)) for brand_id, items in values.items()
    }
    result.update(
        {
            task.brand_id: default
            for task in tasks
            if task.brand_id is not None and task.brand_id not in values
        }
    )
    return result, truncated


def _build_drafts(
    *,
    listings: Sequence[Listing],
    assignments: dict[int, int],
    groups: dict[int, ModelGroup],
    as_of: datetime,
    coverage_by_brand: dict[int, Decimal],
    truncated_brands: set[int],
    degraded: bool,
) -> dict[int, dict[int, MetricDraft]]:
    drafts: dict[int, dict[int, MetricDraft]] = {window: {} for window in WINDOWS}
    for window in WINDOWS:
        cutoff = as_of - timedelta(days=window)
        by_group: dict[int, list[Listing]] = defaultdict(list)
        for listing in listings:
            group_id = assignments[listing.id]
            if _in_window(listing, cutoff, as_of):
                by_group[group_id].append(listing)
        for group_id, group in groups.items():
            candidates = by_group.get(group_id, [])
            usable = [item for item in candidates if not (_flags(item) & _FULL_EXCLUSIONS)]
            active = [item for item in usable if item.status == "active"]
            sold = [item for item in usable if item.status == "sold"]
            exact_sold = [
                item
                for item in sold
                if not item.sold_at_is_estimated and item.days_on_market is not None
            ]
            prices = [
                item.sold_price or item.price
                for item in sold
                if not (_flags(item) & _PRICE_EXCLUSIONS)
            ]
            days = [Decimal(item.days_on_market or 0) for item in exact_sold]
            likes = [Decimal(item.likes_count) for item in exact_sold]
            sell_through = (
                Decimal(len(sold)) / Decimal(len(sold) + len(active))
                if sold or active
                else Decimal(0)
            )
            no_photo = sum("no_photos" in _flags(item) for item in usable)
            excluded = len(candidates) - len(usable)
            penalty = Decimal(excluded) + Decimal(no_photo) * Decimal("0.25")
            quality = (
                max(Decimal(0), Decimal(1) - penalty / Decimal(len(candidates))) * HUNDRED
                if candidates
                else Decimal(0)
            )
            temporal = ratio_score(len(exact_sold), len(sold))
            sample = sample_sufficiency(len(sold), len(active))
            coverage = coverage_by_brand.get(group.brand_id, Decimal(1)) * HUNDRED
            truncated = group.brand_id in truncated_brands
            confidence = confidence_score(
                sample=sample,
                coverage=coverage,
                quality=quality,
                temporal=temporal,
                degraded=degraded,
                truncated=truncated,
            )
            scoring_status = (
                "insufficient_sales"
                if len(sold) < 3
                else "insufficient_temporal_data"
                if len(exact_sold) < 3
                else "scored"
            )
            warnings = sorted(
                {warning for item in candidates for warning in _flags(item) & {"wrong_brand"}}
                | ({"truncated"} if truncated else set())
                | ({"degraded_mode"} if degraded else set())
                | ({"low_sample"} if sample < HUNDRED else set())
                | ({scoring_status} if scoring_status != "scored" else set())
            )
            digest = _digest(group_id, window, candidates, coverage, degraded, truncated)
            median_price = decimal_median(prices)
            median_days = decimal_median(days)
            median_likes = decimal_median(likes)
            drafts[window][group_id] = MetricDraft(
                group_id=group_id,
                brand_id=group.brand_id,
                window_days=window,
                active_count=len(active),
                sold_count=len(sold),
                exact_sold_count=len(exact_sold),
                median_sold_price=(
                    median_price.quantize(TWO_PLACES) if median_price is not None else None
                ),
                median_days_to_sell=(
                    median_days.quantize(TWO_PLACES) if median_days is not None else None
                ),
                median_sold_likes=(
                    median_likes.quantize(TWO_PLACES) if median_likes is not None else None
                ),
                sell_through=sell_through.quantize(SIX_PLACES),
                frequency_score=frequency_score(len(sold), window),
                sell_through_score=(sell_through * HUNDRED).quantize(TWO_PLACES),
                velocity_score=velocity_score(median_days),
                likes_score=engagement_score(median_likes),
                scoring_status=scoring_status,
                confidence_score=confidence,
                confidence_factors={
                    "sample": str(sample),
                    "coverage": str(coverage.quantize(TWO_PLACES)),
                    "quality": str(quality.quantize(TWO_PLACES)),
                    "temporal": str(temporal),
                },
                quality_summary={
                    "candidates": len(candidates),
                    "usable": len(usable),
                    "excluded": excluded,
                    "exact_sold": len(exact_sold),
                    "no_photos": no_photo,
                    "price_excluded": sum(bool(_flags(item) & _PRICE_EXCLUSIONS) for item in sold),
                },
                variant_breakdown={
                    "colors": _variant_rows(usable, "color"),
                    "sizes": _variant_rows(usable, "size_normalized"),
                },
                warnings=warnings,
                input_digest=digest,
            )
    if set(drafts[30]) != set(drafts[90]):
        raise RuntimeError("scoring_window_group_mismatch")
    for group_id, recent in drafts[30].items():
        extended = drafts[90][group_id]
        if recent.sold_count > extended.sold_count:
            raise RuntimeError("scoring_window_sold_not_nested")
        if recent.active_count != extended.active_count:
            raise RuntimeError("scoring_window_active_mismatch")
    return drafts


def _finalize_rows(
    run: ParserRun,
    groups: dict[int, ModelGroup],
    drafts: dict[int, dict[int, MetricDraft]],
    as_of: datetime,
) -> list[ScoringSnapshot]:
    rows: list[ScoringSnapshot] = []
    now = datetime.now(UTC)
    for window, window_drafts in drafts.items():
        by_brand: dict[int, list[MetricDraft]] = defaultdict(list)
        for draft in window_drafts.values():
            by_brand[draft.brand_id].append(draft)
        final = {
            group_id: metrics
            for brand_drafts in by_brand.values()
            for group_id, metrics in finalize_brand(brand_drafts).items()
        }
        for group_id, draft in window_drafts.items():
            metrics = final[group_id]
            rows.append(
                ScoringSnapshot(
                    parser_run_id=run.id,
                    model_group_id=group_id,
                    brand_id=draft.brand_id,
                    model_version=MODEL_VERSION,
                    window_days=window,
                    as_of=as_of,
                    active_count=draft.active_count,
                    sold_count=draft.sold_count,
                    exact_sold_count=draft.exact_sold_count,
                    median_sold_price=draft.median_sold_price,
                    median_days_to_sell=draft.median_days_to_sell,
                    median_sold_likes=draft.median_sold_likes,
                    median_sold_likes_per_day=None,
                    sell_through=draft.sell_through,
                    liquidity_score=metrics.liquidity_score,
                    demand_score=metrics.demand_score,
                    price_score=metrics.price_score,
                    confidence_score=draft.confidence_score,
                    market_opportunity_score=metrics.market_opportunity_score,
                    scoring_status=draft.scoring_status,
                    component_breakdown=metrics.component_breakdown,
                    confidence_factors=draft.confidence_factors,
                    quality_summary=draft.quality_summary,
                    variant_breakdown=draft.variant_breakdown,
                    warnings=draft.warnings,
                    input_digest=draft.input_digest,
                    created_at=now,
                )
            )
    return rows


def _in_window(listing: Listing, cutoff: datetime, as_of: datetime) -> bool:
    if listing.status == "sold":
        timestamp = _aware(listing.sold_at) if listing.sold_at is not None else None
        return timestamp is not None and cutoff <= timestamp <= as_of
    if listing.status == "active":
        return _aware(listing.created_at or listing.first_seen_at) <= as_of
    return False


def _flags(listing: Listing) -> set[str]:
    return set(listing.quality_flags)


def _digest(
    group_id: int,
    window: int,
    listings: Sequence[Listing],
    coverage: Decimal,
    degraded: bool,
    truncated: bool,
) -> str:
    payload = {
        "model_version": MODEL_VERSION,
        "group_id": group_id,
        "window": window,
        "coverage": str(coverage),
        "degraded": degraded,
        "truncated": truncated,
        "listings": [
            {
                "id": item.grailed_id,
                "status": item.status,
                "price": str(item.sold_price or item.price),
                "likes": item.likes_count,
                "days": item.days_on_market,
                "estimated": item.sold_at_is_estimated,
                "size": _variant_value(item.size_normalized),
                "color": _variant_value(item.color),
                "flags": sorted(item.quality_flags),
            }
            for item in sorted(listings, key=lambda candidate: candidate.grailed_id)
        ],
    }
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def _variant_rows(listings: Sequence[Listing], attribute: str) -> list[dict[str, object]]:
    counts: dict[str | None, list[int]] = defaultdict(lambda: [0, 0])
    for listing in listings:
        value = _variant_value(getattr(listing, attribute))
        counts[value][0 if listing.status == "sold" else 1] += 1
    ranked = sorted(
        counts.items(),
        key=lambda item: (
            item[0] is None,
            -item[1][0],
            -(Decimal(item[1][0]) / Decimal(sum(item[1])) if sum(item[1]) else Decimal(0)),
            item[0] or "",
        ),
    )
    return [
        {
            "value": value,
            "sold_count": sold,
            "active_count": active,
            "sell_through": str(
                (Decimal(sold) / Decimal(sold + active)).quantize(SIX_PLACES)
                if sold or active
                else Decimal(0)
            ),
        }
        for value, (sold, active) in ranked
    ]


def _variant_value(value: str | None) -> str | None:
    normalized = " ".join((value or "").casefold().split())
    if not normalized or normalized in {"n/a", "na", "none", "unknown"}:
        return None
    return "gray" if normalized == "grey" else normalized


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _canonical_relistings(
    session: AsyncSession, listings: Sequence[Listing]
) -> list[Listing]:
    """Keep one active/sold row per confirmed same-seller relist component."""

    if not listings:
        return []
    listing_ids = {listing.id for listing in listings}
    brand_ids = {listing.brand_id for listing in listings if listing.brand_id is not None}
    membership_rows = await session.execute(
        select(PhysicalItemMember.listing_id, PhysicalItemMember.physical_item_id)
        .join(Listing, Listing.id == PhysicalItemMember.listing_id)
        .where(Listing.brand_id.in_(brand_ids))
    )
    item_by_listing = {
        listing_id: physical_item_id
        for listing_id, physical_item_id in membership_rows.tuples()
        if listing_id in listing_ids
    }
    by_item: dict[int, list[Listing]] = defaultdict(list)
    result = [listing for listing in listings if listing.id not in item_by_listing]
    for listing in listings:
        if item_id := item_by_listing.get(listing.id):
            by_item[item_id].append(listing)
    for candidates in by_item.values():
        result.append(
            max(
                candidates,
                key=lambda item: (
                    item.status == "sold",
                    item.status == "active",
                    _aware(item.created_at or item.first_seen_at),
                    item.id,
                ),
            )
        )
    return result
