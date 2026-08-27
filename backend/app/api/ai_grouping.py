"""Budgeted controls and public status for AI product grouping."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.errors import ApiError
from app.db.models import (
    AiGroupingItem,
    AiGroupingRun,
    Listing,
    ListingModelAssignment,
    ModelGroup,
)
from app.db.session import get_db
from app.services.ai_grouping.runtime import AiGroupingRuntime
from app.services.ai_grouping.service import GroupingMode

router = APIRouter(prefix="/ai-grouping", tags=["ai-grouping"])
_SAFE_CODE = re.compile(r"[a-z0-9_]{1,80}")


class PreflightResponse(BaseModel):
    mode: GroupingMode
    gemini_configured: bool
    listing_count: int
    unique_input_count: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: Decimal
    budget_cap_usd: Decimal
    can_start: bool
    blocked_reason: str | None = None
    data_fields: list[str]


class StartRequest(BaseModel):
    mode: GroupingMode
    budget_cap_usd: Decimal = Field(gt=0, decimal_places=8)


class ResumeRequest(BaseModel):
    additional_budget_cap_usd: Decimal = Field(default=Decimal(0), ge=0, le=0)


class GroupingExample(BaseModel):
    listing_id: int
    title: str
    old_group: str | None = None
    new_group: str
    product_type: str
    confidence: Decimal


class RunResponse(BaseModel):
    id: int
    mode: GroupingMode
    status: str
    cheap_model: str
    strong_model: str
    prompt_version: str
    grouping_version: str
    budget_cap_usd: Decimal
    estimated_cost_usd: Decimal
    actual_cost_usd: Decimal
    input_tokens: int
    output_tokens: int
    total_items: int
    unique_inputs: int
    resolved_items: int
    ambiguous_items: int
    unique_fallback_items: int
    failed_items: int
    error_code: str | None = None
    warnings: list[str]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    rollback_allowed: bool
    progress_percent: int
    examples: list[GroupingExample]


class RunListResponse(BaseModel):
    data: list[RunResponse]
    total: int
    limit: int
    offset: int


def get_ai_grouping_runtime(request: Request) -> AiGroupingRuntime:
    runtime = getattr(request.app.state, "ai_grouping_runtime", None)
    if not isinstance(runtime, AiGroupingRuntime):
        raise ApiError(503, "ai_grouping_unavailable", "AI grouping runtime is unavailable")
    return runtime


@router.get("/preflight", response_model=PreflightResponse)
async def preflight(
    mode: GroupingMode,
    runtime: Annotated[AiGroupingRuntime, Depends(get_ai_grouping_runtime)],
) -> dict[str, Any]:
    return await runtime.preflight(mode)


@router.post("/runs", response_model=RunResponse, status_code=202)
async def start_run(
    payload: StartRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    runtime: Annotated[AiGroupingRuntime, Depends(get_ai_grouping_runtime)],
) -> RunResponse:
    try:
        run_id = await runtime.create_and_start(payload.mode, payload.budget_cap_usd)
    except (LookupError, RuntimeError, ValueError) as exc:
        raise _api_error(exc) from exc
    return await _require_response(session, run_id, include_examples=True)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunListResponse:
    total = int(await session.scalar(select(func.count()).select_from(AiGroupingRun)) or 0)
    runs = list(
        await session.scalars(
            select(AiGroupingRun).order_by(AiGroupingRun.id.desc()).limit(limit).offset(offset)
        )
    )
    latest = await _latest_completed(session)
    return RunListResponse(
        data=[await _run_response(session, run, latest_completed=latest) for run in runs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=RunResponse)
async def run_detail(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    return await _require_response(session, run_id, include_examples=True)


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    runtime: Annotated[AiGroupingRuntime, Depends(get_ai_grouping_runtime)],
) -> RunResponse:
    try:
        await runtime.cancel(run_id)
    except (LookupError, RuntimeError, ValueError) as exc:
        raise _api_error(exc) from exc
    session.expire_all()
    return await _require_response(session, run_id, include_examples=True)


@router.post("/runs/{run_id}/resume", response_model=RunResponse, status_code=202)
async def resume_run(
    run_id: int,
    _payload: ResumeRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    runtime: Annotated[AiGroupingRuntime, Depends(get_ai_grouping_runtime)],
) -> RunResponse:
    try:
        await runtime.resume(run_id)
    except (LookupError, RuntimeError, ValueError) as exc:
        raise _api_error(exc) from exc
    session.expire_all()
    return await _require_response(session, run_id, include_examples=True)


@router.post("/runs/{run_id}/rollback", response_model=RunResponse)
async def rollback_run(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    runtime: Annotated[AiGroupingRuntime, Depends(get_ai_grouping_runtime)],
) -> RunResponse:
    try:
        await runtime.rollback(run_id)
    except (LookupError, RuntimeError, ValueError) as exc:
        raise _api_error(exc) from exc
    session.expire_all()
    return await _require_response(session, run_id, include_examples=True)


async def _require_response(
    session: AsyncSession, run_id: int, *, include_examples: bool
) -> RunResponse:
    run = await session.get(AiGroupingRun, run_id)
    if run is None:
        raise ApiError(404, "grouping_run_not_found", "AI grouping run does not exist")
    return await _run_response(
        session,
        run,
        include_examples=include_examples,
        latest_completed=await _latest_completed(session),
    )


async def _run_response(
    session: AsyncSession,
    run: AiGroupingRun,
    *,
    include_examples: bool = False,
    latest_completed: int | None = None,
) -> RunResponse:
    stats = run.stats if isinstance(run.stats, dict) else {}
    processed = min(
        run.total_items,
        run.completed_items + run.ambiguous_items + run.failed_items,
    )
    if run.status in {"completed", "rolled_back"}:
        processed = run.total_items
    progress = round(processed * 100 / run.total_items) if run.total_items else 100
    rollback_allowed = False
    if run.status == "completed" and latest_completed == run.id:
        changed = await session.scalar(
            select(func.count())
            .select_from(AiGroupingItem)
            .outerjoin(
                ListingModelAssignment,
                ListingModelAssignment.listing_id == AiGroupingItem.listing_id,
            )
            .where(
                AiGroupingItem.run_id == run.id,
                AiGroupingItem.status == "applied",
                or_(
                    ListingModelAssignment.listing_id.is_(None),
                    ListingModelAssignment.ai_grouping_run_id.is_distinct_from(run.id),
                ),
            )
        )
        rollback_allowed = not bool(changed)
    return RunResponse(
        id=run.id,
        mode=run.mode,  # type: ignore[arg-type]
        status=run.status,
        cheap_model=run.base_model,
        strong_model=run.review_model,
        prompt_version=run.prompt_version,
        grouping_version=run.grouping_version,
        budget_cap_usd=run.budget_limit_usd,
        estimated_cost_usd=run.estimated_cost_usd or Decimal(0),
        actual_cost_usd=run.actual_cost_usd,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        total_items=run.total_items,
        unique_inputs=run.unique_requests,
        resolved_items=run.completed_items,
        ambiguous_items=run.ambiguous_items,
        unique_fallback_items=int(stats.get("unique_fallback_items", 0)),
        failed_items=run.failed_items,
        error_code=_public_code(run.error),
        warnings=[str(value) for value in run.warnings],
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        heartbeat_at=run.heartbeat_at,
        rollback_allowed=rollback_allowed,
        progress_percent=progress,
        examples=await _examples(session, run.id) if include_examples else [],
    )


async def _examples(session: AsyncSession, run_id: int) -> list[GroupingExample]:
    old_group = aliased(ModelGroup)
    rows = (
        await session.execute(
            select(AiGroupingItem, Listing.title, old_group.name)
            .join(Listing, Listing.id == AiGroupingItem.listing_id)
            .outerjoin(old_group, old_group.id == AiGroupingItem.previous_model_group_id)
            .where(
                AiGroupingItem.run_id == run_id,
                AiGroupingItem.target_name.is_not(None),
            )
            .order_by(AiGroupingItem.id)
            .limit(10)
        )
    ).tuples()
    return [
        GroupingExample(
            listing_id=item.listing_id,
            title=title,
            old_group=previous,
            new_group=item.target_name or "",
            product_type=item.product_type or item.target_category or "unknown",
            confidence=item.confidence or Decimal(0),
        )
        for item, title, previous in rows
    ]


async def _latest_completed(session: AsyncSession) -> int | None:
    value = await session.scalar(
        select(AiGroupingRun.id)
        .where(AiGroupingRun.status == "completed")
        .order_by(AiGroupingRun.id.desc())
        .limit(1)
    )
    return int(value) if value is not None else None


def _api_error(exc: Exception) -> ApiError:
    code = _public_code(str(exc)) or "ai_grouping_failed"
    if isinstance(exc, LookupError):
        return ApiError(404, code, "AI grouping run does not exist")
    if code == "gemini_not_configured":
        return ApiError(503, code, "Gemini API key is not configured")
    return ApiError(409, code, "AI grouping request cannot be completed")


def _public_code(value: str | None) -> str | None:
    normalized = str(value or "").casefold()
    return normalized if _SAFE_CODE.fullmatch(normalized) else None
