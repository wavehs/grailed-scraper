"""Database-backed opportunity-v1 scoring service."""

from __future__ import annotations

import hashlib
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

import orjson
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import (
    Brand,
    Listing,
    ModelGroup,
    ModelRule,
    ParserRun,
    ParserRunTask,
    ScoringSnapshot,
)
from app.services.scoring.calculator import (
    FOUR_PLACES,
    HUNDRED,
    SIX_PLACES,
    TWO_PLACES,
    MetricDraft,
    confidence_score,
    decimal_median,
    finalize_brand,
    ratio_score,
    sample_sufficiency,
    velocity_score,
)

MODEL_VERSION = "opportunity-v1"
WINDOWS = (30, 90)
_FULL_EXCLUSIONS = {"possible_replica", "repost"}
_PRICE_EXCLUSIONS = {"price_outlier", "lot_or_bundle"}


class ScoringService(Protocol):
    async def score_run(self, run_id: int) -> dict[str, object]: ...


class OpportunityScoringService:
    """Calculate and persist immutable snapshots from the current listing state."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def score_run(self, run_id: int) -> dict[str, object]:
        async with self._sessions() as session:
            run = await session.get(ParserRun, run_id)
            if run is None:
                raise LookupError(f"Parser run {run_id} does not exist")
            as_of = _aware(run.started_at or run.created_at)
            brands = {
                brand.id: brand
                for brand in await session.scalars(select(Brand).order_by(Brand.id))
            }
            listings = list(
                await session.scalars(
                    select(Listing).where(
                        Listing.brand_id.is_not(None),
                        Listing.status.in_(("active", "sold")),
                    )
                )
            )
            rules = list(
                await session.scalars(
                    select(ModelRule)
                    .where(ModelRule.is_active.is_(True))
                    .options(selectinload(ModelRule.group))
                    .order_by(ModelRule.id)
                )
            )
            assignments, groups = await self._assign_groups(
                session, listings, rules, brands, as_of
            )
            coverage, truncated = await _coverage(session, run_id, run.coverage_avg)
            drafts = _build_drafts(
                listings=listings,
                assignments=assignments,
                groups=groups,
                as_of=as_of,
                coverage_by_brand=coverage,
                truncated_brands=truncated,
                degraded=run.degraded_mode,
            )
            rows = _finalize_rows(run, groups, drafts, as_of)
            await self._persist(session, run_id, rows)
            await session.commit()
            return {
                "status": "completed",
                "model_version": MODEL_VERSION,
                "windows": list(WINDOWS),
                "groups": len(groups),
                "snapshots": len(rows),
            }

    async def _assign_groups(
        self,
        session: AsyncSession,
        listings: Sequence[Listing],
        rules: Sequence[ModelRule],
        brands: dict[int, Brand],
        now: datetime,
    ) -> tuple[dict[int, int], dict[int, ModelGroup]]:
        rules_by_brand: dict[int, list[ModelRule]] = defaultdict(list)
        groups = {rule.group.id: rule.group for rule in rules}
        for rule in rules:
            rules_by_brand[rule.brand_id].append(rule)

        assignments: dict[int, int] = {}
        fallback_needed: dict[str, tuple[int, str]] = {}
        fallback_for_listing: dict[int, str] = {}
        for listing in listings:
            assert listing.brand_id is not None
            matches = [
                rule
                for rule in rules_by_brand[listing.brand_id]
                if rule_matches(rule, listing.title, listing.category)
            ]
            if matches:
                winner = min(matches, key=lambda item: (-len(item.include_keywords), item.id))
                assignments[listing.id] = winner.group_id
                continue
            category = listing.category or "Uncategorized"
            normalized = normalize_text(category) or "uncategorized"
            digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
            stable_key = f"fallback:{listing.brand_id}:{digest}"
            fallback_needed[stable_key] = (listing.brand_id, category)
            fallback_for_listing[listing.id] = stable_key

        existing = {
            group.stable_key: group
            for group in await session.scalars(
                select(ModelGroup).where(ModelGroup.stable_key.in_(fallback_needed))
            )
        } if fallback_needed else {}
        for stable_key, (brand_id, category) in fallback_needed.items():
            group = existing.get(stable_key)
            if group is None:
                brand = brands[brand_id]
                group = ModelGroup(
                    stable_key=stable_key,
                    brand_id=brand_id,
                    name=f"{brand.name} · {category}",
                    category=category,
                    group_type="fallback",
                    created_at=now,
                    updated_at=now,
                )
                session.add(group)
                await session.flush()
                existing[stable_key] = group
            groups[group.id] = group
        for listing_id, stable_key in fallback_for_listing.items():
            assignments[listing_id] = existing[stable_key].id
        return assignments, groups

    @staticmethod
    async def _persist(
        session: AsyncSession, run_id: int, rows: Sequence[ScoringSnapshot]
    ) -> None:
        existing = list(
            await session.scalars(
                select(ScoringSnapshot).where(
                    ScoringSnapshot.parser_run_id == run_id,
                    ScoringSnapshot.model_version == MODEL_VERSION,
                )
            )
        )
        existing_by_key = {
            (row.model_group_id, row.window_days): row for row in existing
        }
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

    async def score_run(self, run_id: int) -> dict[str, object]:
        return {"run_id": run_id, "status": "skipped", "reason": "scoring_disabled"}


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(char for char in decomposed if not unicodedata.combining(char)).split()
    )


def rule_matches(rule: ModelRule, title: str, category: str | None) -> bool:
    normalized_title = normalize_text(title)
    includes = [normalize_text(item) for item in rule.include_keywords if item.strip()]
    excludes = [normalize_text(item) for item in rule.exclude_keywords if item.strip()]
    if rule.category is not None and normalize_text(rule.category) != normalize_text(
        category or ""
    ):
        return False
    return all(item in normalized_title for item in includes) and not any(
        item in normalized_title for item in excludes
    )


async def _coverage(
    session: AsyncSession, run_id: int, fallback: Decimal | None
) -> tuple[dict[int, Decimal], set[int]]:
    tasks = list(
        await session.scalars(
            select(ParserRunTask).where(ParserRunTask.run_id == run_id)
        )
    )
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
        brand_id: sum(items, Decimal(0)) / Decimal(len(items))
        for brand_id, items in values.items()
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
            usable = [
                item
                for item in candidates
                if not (_flags(item) & _FULL_EXCLUSIONS)
            ]
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
            likes = [
                Decimal(item.likes_count) / Decimal(max(item.days_on_market or 0, 1))
                for item in exact_sold
            ]
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
            warnings = sorted(
                {
                    warning
                    for item in candidates
                    for warning in _flags(item) & {"wrong_brand"}
                }
                | ({"truncated"} if truncated else set())
                | ({"degraded_mode"} if degraded else set())
                | ({"low_sample"} if sample < HUNDRED else set())
            )
            digest = _digest(group_id, window, candidates, coverage, degraded, truncated)
            median_price = decimal_median(prices)
            median_days = decimal_median(days)
            median_likes = decimal_median(likes)
            drafts[window][group_id] = MetricDraft(
                group_id=group_id,
                brand_id=group.brand_id,
                active_count=len(active),
                sold_count=len(sold),
                median_sold_price=(
                    median_price.quantize(TWO_PLACES) if median_price is not None else None
                ),
                median_days_to_sell=(
                    median_days.quantize(TWO_PLACES) if median_days is not None else None
                ),
                median_sold_likes_per_day=(
                    median_likes.quantize(FOUR_PLACES)
                    if median_likes is not None
                    else None
                ),
                sell_through=sell_through.quantize(SIX_PLACES),
                sell_through_score=(sell_through * HUNDRED).quantize(TWO_PLACES),
                velocity_score=velocity_score(median_days, window),
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
                    "no_photos": no_photo,
                    "price_excluded": sum(
                        bool(_flags(item) & _PRICE_EXCLUSIONS) for item in sold
                    ),
                },
                warnings=warnings,
                input_digest=digest,
            )
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
                    median_sold_price=draft.median_sold_price,
                    median_days_to_sell=draft.median_days_to_sell,
                    median_sold_likes_per_day=draft.median_sold_likes_per_day,
                    sell_through=draft.sell_through,
                    liquidity_score=metrics.liquidity_score,
                    price_score=metrics.price_score,
                    confidence_score=draft.confidence_score,
                    market_opportunity_score=metrics.market_opportunity_score,
                    component_breakdown=metrics.component_breakdown,
                    confidence_factors=draft.confidence_factors,
                    quality_summary=draft.quality_summary,
                    warnings=draft.warnings,
                    input_digest=draft.input_digest,
                    created_at=now,
                )
            )
    return rows


def _in_window(listing: Listing, cutoff: datetime, as_of: datetime) -> bool:
    if listing.status == "sold":
        timestamp = _aware(listing.sold_at) if listing.sold_at is not None else None
    else:
        timestamp = _aware(listing.created_at or listing.first_seen_at)
    return timestamp is not None and cutoff <= timestamp <= as_of


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
                "flags": sorted(item.quality_flags),
            }
            for item in sorted(listings, key=lambda candidate: candidate.grailed_id)
        ],
    }
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
