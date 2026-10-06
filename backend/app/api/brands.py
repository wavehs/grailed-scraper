"""Brand source-mapping review API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.discovery import ensure_discovery
from app.api.errors import ApiError
from app.api.settings import get_effective_settings
from app.core.config import Settings
from app.db.models import (
    Brand,
    BrandSourceMap,
    GroupMetric,
    Listing,
    ModelGroup,
    ParserRun,
    SourceCredential,
)
from app.db.session import get_db
from app.domain.listings import slugify
from app.repositories.brands import BrandRepository
from app.services.normalization.brands import BrandMappingService, normalize_brand_name
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.models import AlgoliaCredentialsData
from app.services.transport.factory import create_http_transport
from app.services.transport.protocols import HttpTransport

router = APIRouter(prefix="/brands", tags=["brands"])


class MappingResponse(BaseModel):
    id: int
    source_designer_name: str
    source_slug: str | None
    listings_count: int
    match_score: Decimal
    match_method: str
    is_subbrand: bool
    state: Literal["verified", "review", "rejected"]


class BrandResponse(BaseModel):
    id: int
    name: str
    aliases: list[str]
    include_subbrands: bool
    listings_count: int
    status: Literal["verified", "review", "unresolved"]
    mappings: list[MappingResponse]


class BrandListResponse(BaseModel):
    data: list[BrandResponse]


class BrandCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = []
    # Exact Grailed designer chosen from /brands/designers; auto-mapped when omitted.
    designer: str | None = Field(default=None, max_length=255)


class DesignerSuggestion(BaseModel):
    name: str
    listings_count: int


class DesignerSearchResponse(BaseModel):
    data: list[DesignerSuggestion]


class AutoMapRequest(BaseModel):
    brand_ids: list[int] | None = None


class AutoMapResponse(BaseModel):
    processed: int
    verified: int
    review: int
    unresolved: int


class BrandUpdateRequest(BaseModel):
    aliases: list[str] | None = None
    include_subbrands: bool | None = None


class MappingDecisionRequest(BaseModel):
    action: Literal["confirm", "reject"]


@dataclass(slots=True)
class BrandServiceDependency:
    service: BrandMappingService
    repository: BrandRepository
    transport: HttpTransport
    client: AlgoliaClient
    active_index: str
    brand_facet: str


async def get_brand_service(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> AsyncIterator[BrandServiceDependency]:
    repository = BrandRepository(session)
    await ensure_discovery(session, settings)
    transport = create_http_transport(settings)
    cached = await session.scalar(
        select(SourceCredential).where(SourceCredential.source == "grailed")
    )
    if cached is None or cached.active_index is None:
        await transport.close()
        raise ApiError(503, "discovery_incomplete", "Grailed discovery found no listing index")
    credentials = AlgoliaCredentialsData(cached.app_id, cached.api_key, cached.algolia_agent)
    active_index = cached.active_index
    client = AlgoliaClient(
        transport,
        credentials,
        requests_per_minute=settings.requests_per_minute,
        max_concurrency=settings.max_concurrent_requests,
        max_retries=settings.parser_max_retries,
        multiquery_batch_size=settings.algolia_multiquery_batch_size,
        timeout_s=settings.parser_request_timeout_s,
    )
    try:
        brand_facet = cached.brand_facet or "designers.name"
        yield BrandServiceDependency(
            BrandMappingService(
                repository, client, active_index=active_index, brand_facet=brand_facet
            ),
            repository,
            transport,
            client,
            active_index,
            brand_facet,
        )
    finally:
        await transport.close()


@router.get("", response_model=BrandListResponse)
async def list_brands(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> BrandListResponse:
    rows = await BrandRepository(session).list_with_counts()
    return BrandListResponse(data=[_brand_response(brand, count) for brand, count in rows])


@router.get("/designers", response_model=DesignerSearchResponse)
async def search_designers(
    q: Annotated[str, Query(min_length=2, max_length=64)],
    dependency: Annotated[BrandServiceDependency, Depends(get_brand_service)],
) -> DesignerSearchResponse:
    """Suggest exact Grailed designer names with their active listing counts."""

    values = await dependency.client.search_facet_values(
        dependency.active_index, dependency.brand_facet, q
    )
    return DesignerSearchResponse(
        data=[DesignerSuggestion(name=item.value, listings_count=item.count) for item in values]
    )


@router.post("", response_model=BrandResponse, status_code=201)
async def create_brand(
    payload: BrandCreateRequest,
    dependency: Annotated[BrandServiceDependency, Depends(get_brand_service)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> BrandResponse:
    name = " ".join(payload.name.split())
    existing = {normalize_brand_name(brand.name) for brand in await dependency.repository.all()}
    if normalize_brand_name(name) in existing:
        raise ApiError(409, "brand_exists", "This brand is already tracked")
    now = datetime.now(UTC)
    taken = set(await session.scalars(select(Brand.slug)))
    slug = base = slugify(name) or "brand"
    suffix = 2
    while slug in taken:
        slug, suffix = f"{base}-{suffix}", suffix + 1
    brand = Brand(
        name=name,
        slug=slug,
        aliases=list(dict.fromkeys(item.strip() for item in payload.aliases if item.strip())),
        include_subbrands=False,
        created_at=now,
        updated_at=now,
    )
    session.add(brand)
    await session.flush()
    if payload.designer:
        matches = await dependency.client.search_facet_values(
            dependency.active_index, dependency.brand_facet, payload.designer
        )
        exact = next((item for item in matches if item.value == payload.designer), None)
        if exact is None:
            raise ApiError(404, "designer_not_found", "Grailed has no such designer")
        await dependency.repository.upsert_candidate(
            brand_id=brand.id,
            source_name=exact.value,
            listings_count=exact.count,
            score=Decimal(1),
            verified=True,
            is_subbrand=False,
            now=now,
        )
    else:
        await dependency.service.auto_map([brand.id])
    await session.commit()
    created = await dependency.repository.get(brand.id)
    assert created is not None
    return _brand_response(created, 0)


@router.delete("/{brand_id}", status_code=204)
async def delete_brand(
    brand_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Stop tracking a brand and delete its collected listings and analytics."""

    brand = await session.get(Brand, brand_id)
    if brand is None:
        raise ApiError(404, "brand_not_found", "Brand does not exist")
    if await session.scalar(
        select(ParserRun.id).where(ParserRun.status.in_(("pending", "running"))).limit(1)
    ):
        raise ApiError(409, "run_active", "Stop the active collection run first")
    await session.execute(delete(GroupMetric).where(GroupMetric.brand_id == brand_id))
    await session.execute(delete(Listing).where(Listing.brand_id == brand_id))
    await session.execute(delete(ModelGroup).where(ModelGroup.brand_id == brand_id))
    await session.delete(brand)
    await session.commit()
    return Response(status_code=204)


@router.post("/auto-map", response_model=AutoMapResponse)
async def auto_map_brands(
    payload: AutoMapRequest,
    dependency: Annotated[BrandServiceDependency, Depends(get_brand_service)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AutoMapResponse:
    summary = await dependency.service.auto_map(payload.brand_ids)
    await session.commit()
    return AutoMapResponse(**asdict(summary))


@router.patch("/{brand_id}", response_model=BrandResponse)
async def update_brand(
    brand_id: int,
    payload: BrandUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> BrandResponse:
    repository = BrandRepository(session)
    brand = await repository.get(brand_id)
    if brand is None:
        raise ApiError(404, "brand_not_found", "Brand does not exist")
    if payload.aliases is not None:
        brand.aliases = list(
            dict.fromkeys(item.strip() for item in payload.aliases if item.strip())
        )
    if payload.include_subbrands is not None:
        brand.include_subbrands = payload.include_subbrands
    brand.updated_at = datetime.now(UTC)
    await session.commit()
    refreshed = await repository.get(brand_id)
    assert refreshed is not None
    return _brand_response(refreshed, await _listing_count(session, brand_id))


@router.patch("/{brand_id}/mappings/{mapping_id}", response_model=MappingResponse)
async def decide_mapping(
    brand_id: int,
    mapping_id: int,
    payload: MappingDecisionRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MappingResponse:
    mapping = await BrandRepository(session).decide_mapping(
        brand_id, mapping_id, payload.action, datetime.now(UTC)
    )
    if mapping is None:
        raise ApiError(404, "mapping_not_found", "Brand mapping does not exist")
    await session.commit()
    return _mapping_response(mapping)


def _brand_response(brand: Brand, count: int) -> BrandResponse:
    mappings = [_mapping_response(item) for item in brand.source_mappings]
    active = [item for item in mappings if item.state != "rejected"]
    if any(item.state == "verified" for item in active):
        status: Literal["verified", "review", "unresolved"] = "verified"
    elif active:
        status = "review"
    else:
        status = "unresolved"
    return BrandResponse(
        id=brand.id,
        name=brand.name,
        aliases=list(brand.aliases),
        include_subbrands=brand.include_subbrands,
        listings_count=count,
        status=status,
        mappings=mappings,
    )


def _mapping_response(mapping: BrandSourceMap) -> MappingResponse:
    state: Literal["verified", "review", "rejected"]
    if mapping.rejected_at is not None:
        state = "rejected"
    elif mapping.verified:
        state = "verified"
    else:
        state = "review"
    return MappingResponse(
        id=mapping.id,
        source_designer_name=mapping.source_designer_name,
        source_slug=mapping.source_slug,
        listings_count=mapping.listings_count,
        match_score=mapping.match_score,
        match_method=mapping.match_method,
        is_subbrand=mapping.is_subbrand,
        state=state,
    )


async def _listing_count(session: AsyncSession, brand_id: int) -> int:
    from sqlalchemy import func

    from app.db.models import Listing

    return int(
        await session.scalar(select(func.count(Listing.id)).where(Listing.brand_id == brand_id))
        or 0
    )
