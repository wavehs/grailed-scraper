"""Plan one collection run: a full active pass plus sold listings since the last watermark."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.db.models import Brand, BrandSourceMap, SourceCredential, SourceSchema
from app.repositories.lifecycle import LifecycleRepository
from app.services.parser.incremental import IncrementalPlanner
from app.services.sources.grailed.algolia.models import AlgoliaQuery

ACTIVE_KEYS = ("created_at_i", "created_at", "updated_at_i", "updated_at")
SOLD_KEYS = ("sold_at_i", "sold_at", "created_at_i", "created_at")


def collection_filters(
    settings: Settings, index_type: str, now: datetime | None = None
) -> tuple[str, ...]:
    """Optional price band for both indices; sold history is bounded by ``sold_history_days``."""

    filters: list[str] = []
    if settings.collect_price_min_usd is not None:
        filters.append(f"price_i>={settings.collect_price_min_usd}")
    if settings.collect_price_max_usd is not None:
        filters.append(f"price_i<={settings.collect_price_max_usd}")
    if index_type == "sold":
        reference = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
        oldest = int((reference - timedelta(days=settings.sold_history_days)).timestamp())
        filters.append(f"sold_at_i>={oldest}")
    return tuple(filters)


@dataclass(frozen=True, slots=True)
class PlannedTask:
    brand_id: int
    brand_name: str
    index_type: str
    index_name: str
    mode: str
    query: AlgoliaQuery
    can_browse: bool
    sorted_index: str | None
    pagination_limit: int
    key_attrs: tuple[str, ...]

    def persisted(self) -> dict[str, Any]:
        query = asdict(self.query)
        query["facet_filters"] = list(self.query.facet_filters)
        query["numeric_filters"] = list(self.query.numeric_filters)
        query["attributes_to_retrieve"] = list(self.query.attributes_to_retrieve)
        query["facets"] = list(self.query.facets)
        return {
            "brand_id": self.brand_id,
            "index_type": self.index_type,
            "status": "pending",
            "error": None,
            "bucket_spec": {
                "brand_id": self.brand_id,
                "brand_name": self.brand_name,
                "index_type": self.index_type,
                "index_name": self.index_name,
                "mode": self.mode,
                "query": query,
                "can_browse": self.can_browse,
                "sorted_index": self.sorted_index,
                "pagination_limit": self.pagination_limit,
                "key_attrs": list(self.key_attrs),
                # A complete active pass lets us check listings that disappeared.
                "sweep_missing": self.index_type == "active",
            },
        }


@dataclass(frozen=True, slots=True)
class FetchPlan:
    mode: str
    tasks: tuple[PlannedTask, ...]
    warnings: tuple[str, ...]

    @property
    def budget(self) -> dict[str, Any]:
        return {
            "brands": len({task.brand_id for task in self.tasks}),
            "tasks": len(self.tasks),
            "full_brands": sorted(
                {task.brand_name for task in self.tasks if task.mode == "full"}
            ),
        }


class ParserPlanner:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def build(self, *, brand_ids: list[int] | None = None) -> FetchPlan:
        statement = select(Brand).options(selectinload(Brand.source_mappings)).order_by(Brand.id)
        if brand_ids:
            statement = statement.where(Brand.id.in_(brand_ids))
        brands = list(await self._session.scalars(statement))
        if brand_ids and len(brands) != len(set(brand_ids)):
            raise LookupError("One or more brands do not exist")
        if not brands:
            raise RuntimeError("brands_required")
        credential = await self._session.scalar(
            select(SourceCredential).where(SourceCredential.source == "grailed")
        )
        if credential is None:
            raise RuntimeError("discovery_required")
        if not credential.active_index or not credential.sold_index:
            raise RuntimeError("discovery_incomplete")
        schema = await self._session.scalar(
            select(SourceSchema.id).where(SourceSchema.source == "grailed").limit(1)
        )
        if schema is None:
            raise RuntimeError("schema_required")
        can_browse = "browse" in credential.key_acl.get("acl", [])
        pagination_limit = credential.pagination_limit or 1_000
        hits_per_page = min(
            self._settings.algolia_hits_per_page, credential.max_hits_per_page or 1_000
        )
        sorted_indices = list(credential.sorted_indices)
        facet = credential.brand_facet or "designers.name"
        incremental = IncrementalPlanner(LifecycleRepository(self._session), self._settings)
        tasks: list[PlannedTask] = []
        warnings: list[str] = []
        for brand in brands:
            mappings = _verified_mappings(brand.source_mappings, brand.include_subbrands)
            if not mappings:
                warnings.append(f"brand_mapping_required:{brand.name}")
                continue
            facet_group = tuple(f"{facet}:{item.source_designer_name}" for item in mappings)
            for index_type in ("active", "sold"):
                base = AlgoliaQuery(
                    hits_per_page=hits_per_page,
                    facet_filters=(facet_group,),
                    numeric_filters=collection_filters(self._settings, index_type),
                )
                if index_type == "active":
                    query, mode = base, "full"
                else:
                    plan = await incremental.plan(
                        brand_id=brand.id,
                        index_type=index_type,
                        key_attr=SOLD_KEYS[0],
                        query=base,
                        mode="delta",
                    )
                    query = plan.query
                    mode = "delta" if plan.watermark_value is not None else "full"
                tasks.append(
                    PlannedTask(
                        brand_id=brand.id,
                        brand_name=brand.name,
                        index_type=index_type,
                        index_name=(
                            credential.active_index
                            if index_type == "active"
                            else credential.sold_index
                        ),
                        mode=mode,
                        query=query,
                        can_browse=can_browse,
                        sorted_index=_sorted_index(sorted_indices, index_type),
                        pagination_limit=pagination_limit,
                        key_attrs=ACTIVE_KEYS if index_type == "active" else SOLD_KEYS,
                    )
                )
        if not tasks:
            raise RuntimeError("brand_mapping_required")
        first_collection = any(t.mode == "full" and t.index_type == "sold" for t in tasks)
        run_mode = "full" if first_collection else "delta"
        return FetchPlan(run_mode, tuple(tasks), tuple(warnings))


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
