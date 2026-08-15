"""CRUD API for deterministic model-group title rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import ApiError
from app.db.models import Brand, Listing, ModelGroup, ModelRule
from app.db.session import get_db
from app.services.scoring.service import rule_matches

router = APIRouter(prefix="/model-rules", tags=["model-rules"])


class ModelRuleCreate(BaseModel):
    brand_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=255)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    category: str | None = Field(default=None, max_length=255)


class ModelRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    include_keywords: list[str] | None = None
    exclude_keywords: list[str] | None = None
    category: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class ModelRuleResponse(BaseModel):
    id: int
    group_id: int
    brand_id: int
    name: str
    include_keywords: list[str]
    exclude_keywords: list[str]
    category: str | None
    is_active: bool
    matches_count: int


class RuleMatchResponse(BaseModel):
    id: int
    title: str
    status: str


@router.get("", response_model=list[ModelRuleResponse])
async def list_model_rules(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[ModelRuleResponse]:
    rules = list(
        await session.scalars(
            select(ModelRule).options(selectinload(ModelRule.group)).order_by(ModelRule.id)
        )
    )
    return [await _response(session, rule) for rule in rules]


@router.post("", response_model=ModelRuleResponse, status_code=201)
async def create_model_rule(
    payload: ModelRuleCreate,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ModelRuleResponse:
    brand = await session.get(Brand, payload.brand_id)
    if brand is None:
        raise ApiError(404, "brand_not_found", "Brand does not exist")
    now = datetime.now(UTC)
    name = payload.name.strip()
    group = ModelGroup(
        stable_key=f"rule:{uuid4().hex}",
        brand_id=brand.id,
        name=name,
        category=_optional_text(payload.category),
        group_type="rule",
        created_at=now,
        updated_at=now,
    )
    session.add(group)
    await session.flush()
    rule = ModelRule(
        group_id=group.id,
        brand_id=brand.id,
        name=name,
        include_keywords=_keywords(payload.include_keywords),
        exclude_keywords=_keywords(payload.exclude_keywords),
        category=_optional_text(payload.category),
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return await _response(session, rule)


@router.patch("/{rule_id}", response_model=ModelRuleResponse)
async def update_model_rule(
    rule_id: int,
    payload: ModelRuleUpdate,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ModelRuleResponse:
    rule = await session.scalar(
        select(ModelRule).where(ModelRule.id == rule_id).options(selectinload(ModelRule.group))
    )
    if rule is None:
        raise ApiError(404, "model_rule_not_found", "Model rule does not exist")
    if payload.name is not None:
        rule.name = payload.name.strip()
        rule.group.name = rule.name
    if payload.include_keywords is not None:
        rule.include_keywords = _keywords(payload.include_keywords)
    if payload.exclude_keywords is not None:
        rule.exclude_keywords = _keywords(payload.exclude_keywords)
    if "category" in payload.model_fields_set:
        rule.category = _optional_text(payload.category)
        rule.group.category = rule.category
    if payload.is_active is not None:
        rule.is_active = payload.is_active
    now = datetime.now(UTC)
    rule.updated_at = now
    rule.group.updated_at = now
    await session.commit()
    return await _response(session, rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_model_rule(
    rule_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    rule = await session.get(ModelRule, rule_id)
    if rule is None:
        raise ApiError(404, "model_rule_not_found", "Model rule does not exist")
    await session.delete(rule)
    await session.commit()
    return Response(status_code=204)


@router.get("/{rule_id}/matches", response_model=list[RuleMatchResponse])
async def list_rule_matches(
    rule_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[RuleMatchResponse]:
    rule = await session.get(ModelRule, rule_id)
    if rule is None:
        raise ApiError(404, "model_rule_not_found", "Model rule does not exist")
    listings = list(
        await session.scalars(
            select(Listing).where(Listing.brand_id == rule.brand_id).order_by(Listing.id)
        )
    )
    return [
        RuleMatchResponse(id=item.id, title=item.title, status=item.status)
        for item in listings
        if rule_matches(rule, item.title, item.category)
    ]


async def _response(session: AsyncSession, rule: ModelRule) -> ModelRuleResponse:
    count = int(
        await session.scalar(
            select(func.count(Listing.id)).where(Listing.brand_id == rule.brand_id)
        )
        or 0
    )
    if count:
        listings = list(
            await session.scalars(select(Listing).where(Listing.brand_id == rule.brand_id))
        )
        count = sum(rule_matches(rule, item.title, item.category) for item in listings)
    return ModelRuleResponse(
        id=rule.id,
        group_id=rule.group_id,
        brand_id=rule.brand_id,
        name=rule.name,
        include_keywords=list(rule.include_keywords),
        exclude_keywords=list(rule.exclude_keywords),
        category=rule.category,
        is_active=rule.is_active,
        matches_count=count,
    )


def _keywords(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def _optional_text(value: str | None) -> str | None:
    stripped = value.strip() if value is not None else ""
    return stripped or None
