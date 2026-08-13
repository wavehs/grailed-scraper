"""Incremental query planning and refresh-active execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from app.core.config import Settings
from app.db.models import Listing
from app.domain.listings import FetchTier, ListingStatus
from app.repositories.lifecycle import LifecycleRepository
from app.repositories.listings import ListingRepository
from app.services.normalization.normalizer import ListingNormalizer, NormalizationContext
from app.services.parser.fetching import FetchApi
from app.services.sources.base.models import RawHit
from app.services.sources.grailed.algolia.models import AlgoliaQuery


@dataclass(frozen=True, slots=True)
class IncrementalPlan:
    mode: str
    query: AlgoliaQuery
    watermark_value: str | None


@dataclass(frozen=True, slots=True)
class RefreshActiveResult:
    checked: int
    active: int
    sold: int
    pending: int
    removed: int


class IncrementalPlanner:
    def __init__(self, repository: LifecycleRepository, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings

    async def plan(
        self,
        *,
        brand_id: int,
        index_type: str,
        key_attr: str,
        query: AlgoliaQuery,
        mode: str | None = None,
    ) -> IncrementalPlan:
        selected_mode = mode or self._settings.parser_mode
        watermark = await self._repository.watermark("grailed", brand_id, index_type)
        if selected_mode == "full" or watermark is None or watermark.last_key_value is None:
            return IncrementalPlan(selected_mode, query, None)
        try:
            lower_bound = Decimal(watermark.last_key_value) - Decimal(
                self._settings.parser_watermark_overlap_hours * 3600
            )
        except InvalidOperation:
            return IncrementalPlan(selected_mode, query, watermark.last_key_value)
        numeric = (*query.numeric_filters, f"{key_attr}>{format(lower_bound, 'f')}")
        return IncrementalPlan(
            selected_mode,
            replace(query, numeric_filters=numeric),
            watermark.last_key_value,
        )

    async def complete(
        self,
        *,
        brand_id: int,
        index_type: str,
        last_key_value: str | None,
        mode: str,
        coverage_complete: bool,
        truncated: bool,
        now: datetime | None = None,
    ) -> bool:
        if last_key_value is None or not coverage_complete or truncated:
            return False
        await self._repository.advance_watermark(
            source="grailed",
            brand_id=brand_id,
            index_type=index_type,
            last_key_value=last_key_value,
            mode=mode,
            now=now or datetime.now(UTC),
        )
        return True


class RefreshActiveService:
    batch_size = 100

    def __init__(
        self,
        fetcher: FetchApi,
        lifecycle: LifecycleRepository,
        listings: ListingRepository,
        normalizer: ListingNormalizer,
        settings: Settings,
        *,
        active_index: str,
        sold_index: str,
    ) -> None:
        self._fetcher = fetcher
        self._lifecycle = lifecycle
        self._listings = listings
        self._normalizer = normalizer
        self._settings = settings
        self._active_index = active_index
        self._sold_index = sold_index

    async def run(
        self,
        *,
        parser_run_id: int,
        brand_id: int | None = None,
        now: datetime | None = None,
    ) -> RefreshActiveResult:
        observed = now or datetime.now(UTC)
        candidates = await self._lifecycle.refresh_candidates(brand_id)
        active_count = sold_count = pending_count = removed_count = 0
        for offset in range(0, len(candidates), self.batch_size):
            batch = candidates[offset : offset + self.batch_size]
            by_id = {item.grailed_id: item for item in batch}
            active_page = await self._fetcher.search(
                self._active_index, _id_query(tuple(by_id))
            )
            active_ids = {
                identifier
                for hit in active_page.hits
                if (identifier := _hit_id(hit)) is not None
            }
            active_count += len(active_ids)
            await self._normalize_and_upsert(
                active_page.hits, "active", parser_run_id, observed, by_id
            )
            missing_ids = tuple(identifier for identifier in by_id if identifier not in active_ids)
            sold_ids: set[int] = set()
            if missing_ids:
                sold_page = await self._fetcher.search(self._sold_index, _id_query(missing_ids))
                sold_ids = {
                    identifier
                    for hit in sold_page.hits
                    if (identifier := _hit_id(hit)) is not None
                }
                sold_count += len(sold_ids)
                await self._normalize_and_upsert(
                    sold_page.hits, "sold", parser_run_id, observed, by_id
                )
            still_missing = [
                by_id[item_id] for item_id in missing_ids if item_id not in sold_ids
            ]
            pending, removed = await self._lifecycle.apply_missing(
                still_missing,
                now=observed,
                confirm_after=timedelta(hours=self._settings.parser_removed_confirm_hours),
            )
            pending_count += pending
            removed_count += removed
        return RefreshActiveResult(
            len(candidates), active_count, sold_count, pending_count, removed_count
        )

    async def _normalize_and_upsert(
        self,
        hits: tuple[dict[str, object], ...],
        status: str,
        parser_run_id: int,
        observed: datetime,
        existing: Mapping[int, Listing],
    ) -> None:
        normalized = []
        tier = _tier(self._fetcher)
        for payload in hits:
            identifier = _hit_id(payload)
            prior = existing.get(identifier or -1)
            result = await self._normalizer.normalize(
                RawHit(dict(payload), tier),
                NormalizationContext(
                    status=cast(ListingStatus, status),
                    parser_run_id=parser_run_id,
                    observed_at=observed,
                    brand_id=getattr(prior, "brand_id", None),
                    fetch_tier=tier,
                ),
            )
            if result.listing is not None:
                normalized.append(
                    result.listing.model_copy(
                        update={
                            "quality_flags": list(getattr(prior, "quality_flags", [])),
                            "removed_checked_at": None,
                        }
                    )
                )
        if normalized:
            await self._listings.upsert_batch(normalized)


def _id_query(ids: tuple[int, ...]) -> AlgoliaQuery:
    filters = " OR ".join(f"objectID:{identifier}" for identifier in ids)
    return AlgoliaQuery(hits_per_page=len(ids), filters=filters)


def _hit_id(payload: Mapping[str, Any]) -> int | None:
    value = payload.get("objectID", payload.get("id"))
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tier(fetcher: FetchApi) -> FetchTier:
    value = getattr(fetcher, "current_tier", "T1")
    return cast(FetchTier, value) if value in {"T0", "T1", "T2", "T3"} else "T1"
