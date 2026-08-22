"""Read-only pre-flight planning for parser runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import ceil
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.db.models import Brand, BrandSourceMap, SourceCredential, SourceSchema
from app.repositories.lifecycle import LifecycleRepository
from app.services.parser.incremental import IncrementalPlanner
from app.services.sources.grailed.algolia.models import AlgoliaPage, AlgoliaQuery, AlgoliaRequest


def listing_numeric_filters(now: datetime | None = None) -> tuple[str, ...]:
    reference = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    created_after = int((reference - timedelta(days=120)).timestamp())
    return (f"created_at_i>={created_after}", "price_i>=400", "price_i<=5000")


class QueryProbe(Protocol):
    async def multi_query(self, requests: list[AlgoliaRequest]) -> tuple[AlgoliaPage, ...]: ...


@dataclass(frozen=True, slots=True)
class PlannedTask:
    brand_id: int
    brand_name: str
    index_type: str
    index_name: str
    query: AlgoliaQuery
    can_browse: bool
    sorted_index: str | None
    pagination_limit: int
    key_attrs: tuple[str, ...]
    max_hits: int
    status: str = "pending"
    error: str | None = None

    def persisted(self) -> dict[str, Any]:
        query = asdict(self.query)
        query["facet_filters"] = list(self.query.facet_filters)
        query["numeric_filters"] = list(self.query.numeric_filters)
        query["attributes_to_retrieve"] = list(self.query.attributes_to_retrieve)
        query["facets"] = list(self.query.facets)
        return {
            "brand_id": self.brand_id,
            "index_type": self.index_type,
            "status": self.status,
            "error": self.error,
            "bucket_spec": {
                "brand_id": self.brand_id,
                "brand_name": self.brand_name,
                "index_type": self.index_type,
                "index_name": self.index_name,
                "query": query,
                "can_browse": self.can_browse,
                "sorted_index": self.sorted_index,
                "pagination_limit": self.pagination_limit,
                "key_attrs": list(self.key_attrs),
                "max_hits": self.max_hits,
            },
        }


@dataclass(frozen=True, slots=True)
class FetchPlan:
    mode: str
    tasks: tuple[PlannedTask, ...]
    budget: dict[str, Any]
    warnings: tuple[str, ...]

    def digest(self) -> str:
        payload = {
            "mode": self.mode,
            "limit": self.budget["limit"],
            "tasks": [item.persisted() for item in self.tasks],
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def public(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "confirmation_token": self.digest(),
            "budget": self.budget,
            "warnings": list(self.warnings),
            "tasks": [
                {
                    "brand_id": item.brand_id,
                    "brand": item.brand_name,
                    "index_type": item.index_type,
                    "index": item.index_name,
                    "status": item.status,
                    "max_hits": item.max_hits,
                    "strategy": "browse"
                    if item.can_browse
                    else ("keyset" if item.sorted_index else "range_split"),
                }
                for item in self.tasks
            ],
        }


class ParserPlanner:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def build(self, *, mode: str, brand_ids: list[int] | None = None) -> FetchPlan:
        statement = select(Brand).options(selectinload(Brand.source_mappings)).order_by(Brand.id)
        if brand_ids:
            statement = statement.where(Brand.id.in_(brand_ids))
        brands = list(await self._session.scalars(statement))
        if brand_ids and len(brands) != len(set(brand_ids)):
            raise LookupError("One or more brands do not exist")
        credential = await self._session.scalar(
            select(SourceCredential).where(SourceCredential.source == "grailed")
        )
        if credential is None:
            raise RuntimeError("discovery_required")
        active_index = credential.active_index
        sold_index = credential.sold_index
        if not active_index or not sold_index:
            raise RuntimeError("discovery_incomplete")
        schema = await self._session.scalar(
            select(SourceSchema.id)
            .where(SourceSchema.source == "grailed")
            .order_by(SourceSchema.detected_at.desc())
            .limit(1)
        )
        if schema is None:
            raise RuntimeError("schema_required")
        can_browse = "browse" in credential.key_acl.get("acl", [])
        pagination_limit = credential.pagination_limit
        pagination_limit = pagination_limit or 1_000
        sorted_indices = list(credential.sorted_indices)
        facet = credential.brand_facet or "designers.name"
        tasks: list[PlannedTask] = []
        warnings: list[str] = []
        estimated_hits = 0
        incremental = IncrementalPlanner(LifecycleRepository(self._session), self._settings)
        index_types = ("active",) if mode == "refresh_active" else ("active", "sold")
        per_task_limit, remainder = divmod(
            self._settings.parser_max_items_per_brand, len(index_types)
        )
        numeric_filters = listing_numeric_filters()
        for brand in brands:
            mappings = _verified_mappings(brand.source_mappings, brand.include_subbrands)
            if not mappings:
                raise RuntimeError("brand_mapping_required")
            facet_group = tuple(f"{facet}:{item.source_designer_name}" for item in mappings)
            estimated_hits += sum(item.listings_count for item in mappings)
            for position, index_type in enumerate(index_types):
                index_name = active_index if index_type == "active" else sold_index
                key_attrs = (
                    ("created_at_i", "created_at", "updated_at_i", "updated_at")
                    if index_type == "active"
                    else ("sold_at_i", "sold_at", "created_at_i", "created_at")
                )
                query = AlgoliaQuery(
                    hits_per_page=self._settings.algolia_hits_per_page,
                    facet_filters=(facet_group,) if facet_group else (),
                    numeric_filters=numeric_filters,
                )
                if mode == "delta" and mappings:
                    query = (
                        await incremental.plan(
                            brand_id=brand.id,
                            index_type=index_type,
                            key_attr=key_attrs[0],
                            query=query,
                            mode=mode,
                        )
                    ).query
                tasks.append(
                    PlannedTask(
                        brand_id=brand.id,
                        brand_name=brand.name,
                        index_type=index_type,
                        index_name=index_name,
                        query=query,
                        can_browse=can_browse,
                        sorted_index=_sorted_index(sorted_indices, index_type),
                        pagination_limit=pagination_limit,
                        key_attrs=key_attrs,
                        max_hits=per_task_limit + (position < remainder),
                        status="pending",
                        error=None,
                    )
                )
        runnable = sum(item.status == "pending" for item in tasks)
        bounded_hits = min(estimated_hits, len(tasks) * per_task_limit + len(brands) * remainder)
        page_requests = ceil(bounded_hits / max(self._settings.algolia_hits_per_page, 1))
        estimated_requests = runnable * 16 + page_requests
        budget = {
            "brands": len(brands),
            "tasks": len(tasks),
            "runnable_tasks": runnable,
            "estimated_hits": estimated_hits,
            "bounded_hits": bounded_hits,
            "max_items_per_brand": self._settings.parser_max_items_per_brand,
            "estimated_requests": estimated_requests,
            "limit": self._settings.parser_max_requests_per_run,
            "over_limit": estimated_requests > self._settings.parser_max_requests_per_run,
        }
        if estimated_hits > bounded_hits:
            warnings.append("Collection is bounded per brand; coverage will be partial")
        return FetchPlan(mode, tuple(tasks), budget, tuple(warnings))

    async def probe(self, plan: FetchPlan, client: QueryProbe) -> FetchPlan:
        """Replace mapping-count estimates with bounded live zero-hit probes."""

        requests = [
            AlgoliaRequest(item.index_name, replace(item.query, hits_per_page=0, page=0))
            for item in plan.tasks
            if item.status == "pending"
        ]
        pages = await client.multi_query(requests)
        estimated_hits = sum(page.nb_hits for page in pages)
        probe_requests = ceil(len(requests) / self._settings.algolia_multiquery_batch_size)
        bounded_hits = sum(
            min(page.nb_hits, task.max_hits) for page, task in zip(pages, plan.tasks, strict=True)
        )
        page_requests = ceil(bounded_hits / max(self._settings.algolia_hits_per_page, 1))
        # Adaptive range pagination needs bounded zero-hit probes before data pages.
        estimated_requests = probe_requests + len(requests) * 16 + page_requests
        budget = {
            **plan.budget,
            "estimated_hits": estimated_hits,
            "bounded_hits": bounded_hits,
            "estimated_requests": estimated_requests,
            "over_limit": estimated_requests > self._settings.parser_max_requests_per_run,
        }
        warnings = list(plan.warnings)
        if any(not page.exhaustive_nb_hits for page in pages):
            warnings.append("Algolia returned a non-exhaustive dry-run estimate")
        return FetchPlan(plan.mode, plan.tasks, budget, tuple(warnings))


def _verified_mappings(
    mappings: list[BrandSourceMap], include_subbrands: bool
) -> list[BrandSourceMap]:
    return [
        item
        for item in mappings
        if item.verified
        and item.rejected_at is None
        and (include_subbrands or not item.is_subbrand)
    ]


def _sorted_index(indices: list[str], index_type: str) -> str | None:
    token = "sold" if index_type == "sold" else "date"
    return next((name for name in indices if token in name.casefold()), None)
