"""Model groups: taxonomy, regrouping and manual edits stored as durable rules."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.db.models import (
    Brand,
    BrandStopword,
    Listing,
    ListingModelAssignment,
    ListingOverride,
    ModelGroup,
)
from app.db.session import get_db
from app.services.grouping import GroupingService
from app.services.grouping.kinds import NONE_SLUG
from app.services.grouping.normalize import TitleNormalizer
from app.services.grouping.policy import load_policy
from app.services.metrics import MetricsService

router = APIRouter(tags=["groups"])
_fallback_lock = asyncio.Lock()


class SectionInfo(BaseModel):
    id: str
    ru: str
    en: str


class TypeInfo(BaseModel):
    id: str
    section: str
    ru: str
    en: str


class TaxonomyResponse(BaseModel):
    version: str
    sections: list[SectionInfo]
    types: list[TypeInfo]


class RegroupRequest(BaseModel):
    brand_ids: list[int] | None = None
    full: bool = True


class GroupSummary(BaseModel):
    id: int
    brand_id: int
    brand: str
    product_type: str
    slug: str
    name: str
    aliases: list[str]
    parent_id: int | None
    status: Literal["confirmed", "auto", "ignored"]
    source: str
    is_fallback: bool
    listings: int = 0
    sold: int = 0
    active: int = 0


class GroupListResponse(BaseModel):
    data: list[GroupSummary]
    total: int


class GroupDetail(GroupSummary):
    parent: GroupSummary | None = None
    versions: list[GroupSummary] = []


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: int | None = None
    status: Literal["confirmed"] | None = None


class MergeRequest(BaseModel):
    target_id: int


class SplitRequest(BaseModel):
    phrase: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    as_version: bool = True


class ListingGroupRequest(BaseModel):
    group_id: int | None


@router.get("/grouping/taxonomy", response_model=TaxonomyResponse)
async def taxonomy() -> TaxonomyResponse:
    policy = load_policy().taxonomy
    return TaxonomyResponse(
        version=policy.version,
        sections=[
            SectionInfo(id=key, ru=value["ru"], en=value["en"])
            for key, value in policy.sections.items()
        ],
        types=[
            TypeInfo(id=item.id, section=item.section, ru=item.ru, en=item.en)
            for item in policy.types.values()
        ],
    )


@router.post("/grouping/regroup")
async def regroup(
    payload: RegroupRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    async with _lock(request):
        result = await GroupingService(session).regroup(payload.brand_ids, full=payload.full)
        await session.commit()
    await _refresh_metrics(session, {item.brand_id for item in result.brands})
    return result.summary()


@router.get("/groups", response_model=GroupListResponse)
async def list_groups(
    session: Annotated[AsyncSession, Depends(get_db)],
    brand_id: int | None = None,
    product_type: Annotated[str | None, Query(max_length=32)] = None,
    status: Literal["confirmed", "auto", "ignored"] | None = None,
    search: Annotated[str, Query(max_length=200)] = "",
    lines_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> GroupListResponse:
    statement = select(ModelGroup, Brand.name).join(Brand, Brand.id == ModelGroup.brand_id)
    if brand_id is not None:
        statement = statement.where(ModelGroup.brand_id == brand_id)
    if product_type:
        statement = statement.where(ModelGroup.product_type == product_type)
    if status:
        statement = statement.where(ModelGroup.status == status)
    else:
        statement = statement.where(ModelGroup.status != "ignored")
    if lines_only:
        statement = statement.where(ModelGroup.parent_id.is_(None))
    if search.strip():
        term = search.strip().casefold()
        statement = statement.where(func.lower(ModelGroup.name).contains(term, autoescape=True))
    total = int(
        await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    )
    rows = list(
        await session.execute(
            statement.order_by(Brand.name, ModelGroup.product_type, ModelGroup.name)
            .limit(limit)
            .offset(offset)
        )
    )
    counts = await _counts(session, [group.id for group, _ in rows])
    return GroupListResponse(
        data=[_summary(group, brand, counts) for group, brand in rows], total=total
    )


@router.get("/groups/{group_id}", response_model=GroupDetail)
async def group_detail(
    group_id: int, session: Annotated[AsyncSession, Depends(get_db)]
) -> GroupDetail:
    return await group_detail_data(session, await _group(session, group_id))


@router.patch("/groups/{group_id}", response_model=GroupDetail)
async def update_group(
    group_id: int,
    payload: GroupPatch,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> GroupDetail:
    group = await _editable(session, group_id)
    now = datetime.now(UTC)
    if payload.name is not None and payload.name.strip() != group.name:
        if group.name not in group.aliases:
            group.aliases = [*group.aliases, group.name]
        group.name = payload.name.strip()
        group.status = "confirmed"
        group.source = "user" if group.source == "mined" else group.source
    if "parent_id" in payload.model_fields_set:
        await _set_parent(session, group, payload.parent_id)
    if payload.status == "confirmed":
        group.status = "confirmed"
    group.updated_at = now
    await _apply(request, session, group.brand_id)
    return await group_detail_data(session, group)


@router.post("/groups/{group_id}/merge", response_model=GroupDetail)
async def merge_groups(
    group_id: int,
    payload: MergeRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> GroupDetail:
    source = await _editable(session, group_id)
    target = await _editable(session, payload.target_id)
    if source.id == target.id:
        raise ApiError(422, "merge_into_self", "A group cannot be merged into itself")
    if (source.brand_id, source.product_type) != (target.brand_id, target.product_type):
        raise ApiError(409, "merge_scope", "Only groups of one brand and product type can merge")
    target.aliases = [
        item
        for item in dict.fromkeys([*target.aliases, source.name, *source.aliases])
        if item != target.name
    ]
    target.status = "confirmed"
    new_parent = target.parent_id or target.id
    await session.execute(
        update(ModelGroup)
        .where(ModelGroup.parent_id == source.id)
        .values(parent_id=new_parent if new_parent != source.id else None)
    )
    await session.execute(
        update(ListingOverride)
        .where(ListingOverride.model_group_id == source.id)
        .values(model_group_id=target.id)
    )
    # A tombstone keeps the phrase from being re-seeded or re-mined as its own group.
    source.status = "ignored"
    source.parent_id = None
    source.updated_at = target.updated_at = datetime.now(UTC)
    await _apply(request, session, target.brand_id)
    return await group_detail_data(session, target)


@router.post("/groups/{group_id}/split", response_model=GroupDetail, status_code=201)
async def split_group(
    group_id: int,
    payload: SplitRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> GroupDetail:
    source = await _group(session, group_id)
    if source.status == "ignored":
        raise ApiError(409, "group_ignored", "This group is no longer used")
    brand = await session.get(Brand, source.brand_id)
    assert brand is not None
    tokens = TitleNormalizer(
        load_policy(), brand_terms=[brand.name, *brand.aliases], brand_slug=brand.slug
    ).phrase(payload.phrase)
    if not tokens:
        raise ApiError(
            422, "phrase_empty", "The phrase has only brand, type, color or size words"
        )
    slug = "-".join(tokens)
    existing = await session.scalar(
        select(ModelGroup).where(
            ModelGroup.brand_id == source.brand_id,
            ModelGroup.product_type == source.product_type,
            ModelGroup.slug == slug,
        )
    )
    parent_id: int | None = None
    if payload.as_version and source.slug != NONE_SLUG:
        parent_id = source.parent_id or source.id
    now = datetime.now(UTC)
    name = (payload.name or payload.phrase).strip()
    if existing is not None and existing.status != "ignored":
        raise ApiError(409, "group_exists", "A group with this phrase already exists")
    if existing is not None:
        existing.status = "confirmed"
        existing.source = "user"
        existing.name = name
        existing.parent_id = parent_id
        existing.updated_at = now
        created = existing
    else:
        created = ModelGroup(
            brand_id=source.brand_id,
            product_type=source.product_type,
            slug=slug,
            name=name,
            aliases=[payload.phrase.strip()] if payload.phrase.strip() != name else [],
            parent_id=parent_id,
            status="confirmed",
            source="user",
            created_at=now,
            updated_at=now,
        )
        session.add(created)
    await session.flush()
    await _apply(request, session, source.brand_id)
    return await group_detail_data(session, created)


@router.post("/groups/{group_id}/not-model", response_model=GroupSummary)
async def mark_not_model(
    group_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> GroupSummary:
    group = await _editable(session, group_id)
    now = datetime.now(UTC)
    known = set(
        await session.scalars(
            select(BrandStopword.phrase).where(BrandStopword.brand_id == group.brand_id)
        )
    )
    for phrase in dict.fromkeys([group.name, *group.aliases]):
        if phrase not in known:
            session.add(BrandStopword(brand_id=group.brand_id, phrase=phrase, created_at=now))
    await session.execute(
        update(ModelGroup).where(ModelGroup.parent_id == group.id).values(parent_id=None)
    )
    group.status = "ignored"
    group.parent_id = None
    group.updated_at = now
    await _apply(request, session, group.brand_id)
    brand = await session.get(Brand, group.brand_id)
    return _summary(group, brand.name if brand else "", {})


@router.put("/listings/{listing_id}/group", response_model=GroupDetail | None)
async def move_listing(
    listing_id: int,
    payload: ListingGroupRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> GroupDetail | None:
    listing = await session.get(Listing, listing_id)
    if listing is None or listing.brand_id is None:
        raise ApiError(404, "listing_not_found", "Listing does not exist")
    override = await session.get(ListingOverride, listing_id)
    if payload.group_id is None:
        if override is not None:
            await session.delete(override)
        await _apply(request, session, listing.brand_id)
        return None
    target = await _group(session, payload.group_id)
    if target.brand_id != listing.brand_id or target.status == "ignored":
        raise ApiError(409, "group_scope", "The group belongs to another brand or is unused")
    if override is None:
        session.add(
            ListingOverride(
                listing_id=listing_id, model_group_id=target.id, created_at=datetime.now(UTC)
            )
        )
    else:
        override.model_group_id = target.id
    await _apply(request, session, listing.brand_id)
    return await group_detail_data(session, target)


async def _group(session: AsyncSession, group_id: int) -> ModelGroup:
    group = await session.get(ModelGroup, group_id)
    if group is None:
        raise ApiError(404, "group_not_found", "Model group does not exist")
    return group


async def _editable(session: AsyncSession, group_id: int) -> ModelGroup:
    group = await _group(session, group_id)
    if group.slug == NONE_SLUG:
        raise ApiError(409, "service_group", "The No model group cannot be edited")
    if group.status == "ignored":
        raise ApiError(409, "group_ignored", "This group is no longer used")
    return group


async def _set_parent(session: AsyncSession, group: ModelGroup, parent_id: int | None) -> None:
    if parent_id is None:
        group.parent_id = None
        return
    parent = await _editable(session, parent_id)
    if parent.id == group.id:
        raise ApiError(422, "parent_self", "A group cannot be its own line")
    if (parent.brand_id, parent.product_type) != (group.brand_id, group.product_type):
        raise ApiError(409, "parent_scope", "The line must have the same brand and type")
    if parent.parent_id is not None:
        raise ApiError(409, "parent_is_version", "A version cannot hold other versions")
    has_versions = await session.scalar(
        select(ModelGroup.id)
        .where(ModelGroup.parent_id == group.id, ModelGroup.status != "ignored")
        .limit(1)
    )
    if has_versions:
        raise ApiError(409, "group_has_versions", "Move this line's versions first")
    group.parent_id = parent.id


def _lock(request: Request) -> asyncio.Lock:
    runtime = getattr(request.app.state, "parser_runtime", None)
    lock = getattr(runtime, "grouping_lock", None)
    return lock if isinstance(lock, asyncio.Lock) else _fallback_lock


async def _apply(request: Request, session: AsyncSession, brand_id: int) -> None:
    """Persist the rule, regroup the brand from scratch and refresh its metrics."""

    async with _lock(request):
        await session.flush()
        await GroupingService(session).regroup([brand_id], full=True)
        await session.commit()
    await _refresh_metrics(session, {brand_id})


async def _refresh_metrics(session: AsyncSession, brand_ids: set[int]) -> None:
    if brand_ids:
        await MetricsService(session).recompute(brand_ids)
        await session.commit()


async def _counts(session: AsyncSession, group_ids: list[int]) -> dict[int, dict[str, int]]:
    if not group_ids:
        return {}
    counts: dict[int, dict[str, int]] = {}
    rows = await session.execute(
        select(ListingModelAssignment.model_group_id, Listing.status, func.count())
        .join(Listing, Listing.id == ListingModelAssignment.listing_id)
        .where(ListingModelAssignment.model_group_id.in_(group_ids))
        .group_by(ListingModelAssignment.model_group_id, Listing.status)
    )
    for group_id, status, count in rows:
        counts.setdefault(group_id, {})[status] = int(count)
    return counts


def _summary(group: ModelGroup, brand: str, counts: dict[int, dict[str, int]]) -> GroupSummary:
    by_status = counts.get(group.id, {})
    return GroupSummary(
        id=group.id,
        brand_id=group.brand_id,
        brand=brand,
        product_type=group.product_type,
        slug=group.slug,
        name=group.name,
        aliases=list(group.aliases),
        parent_id=group.parent_id,
        status=group.status,  # type: ignore[arg-type]
        source=group.source,
        is_fallback=group.slug == NONE_SLUG,
        listings=sum(by_status.values()),
        sold=by_status.get("sold", 0),
        active=by_status.get("active", 0),
    )


async def group_detail_data(session: AsyncSession, group: ModelGroup) -> GroupDetail:
    await session.refresh(group)
    brand = await session.get(Brand, group.brand_id)
    brand_name = brand.name if brand else ""
    versions = list(
        await session.scalars(
            select(ModelGroup)
            .where(ModelGroup.parent_id == group.id, ModelGroup.status != "ignored")
            .order_by(ModelGroup.name)
        )
    )
    parent = await session.get(ModelGroup, group.parent_id) if group.parent_id else None
    ids = [group.id, *(item.id for item in versions), *([parent.id] if parent else [])]
    counts = await _counts(session, ids)
    summary = _summary(group, brand_name, counts)
    return GroupDetail(
        **summary.model_dump(),
        parent=_summary(parent, brand_name, counts) if parent else None,
        versions=[_summary(item, brand_name, counts) for item in versions],
    )
