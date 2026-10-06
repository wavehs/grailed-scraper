"""Parser run control, progress, reporting, and health API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import metadata
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.discovery import (
    DiscoveryRefreshRequest,
    DiscoveryResponse,
    ensure_discovery,
    get_discovery_service,
    refresh_discovery,
)
from app.api.errors import ApiError
from app.api.settings import get_effective_settings
from app.core.config import Settings
from app.core.privacy import compliance_reasons, require_live_compliance
from app.db.models import (
    Brand,
    GroupMetric,
    Listing,
    ModelGroup,
    ParserRun,
    ParserRunTask,
    ParserWatermark,
    SchemaAlert,
    SourceCredential,
    SourceSchema,
    UnmatchedBrand,
)
from app.db.session import get_db
from app.repositories.runs import RunRepository
from app.services.parser.planner import ParserPlanner
from app.services.parser.runtime import ParserRuntime
from app.services.sources.grailed.discovery.service import DiscoveryService

router = APIRouter(prefix="/parser", tags=["parser"])
RunStatus = Literal[
    "pending", "running", "completed", "partial", "failed", "interrupted", "cancelled"
]


class RunRequest(BaseModel):
    """Collect selected brands (all when omitted): full on first run, delta afterwards."""

    brand_ids: list[int] | None = None


class ClearDataRequest(BaseModel):
    confirm: Literal[True]


class ClearDataResponse(BaseModel):
    listings_deleted: int
    runs_deleted: int


class ClearHistoryResponse(BaseModel):
    runs_deleted: int


class RunSummary(BaseModel):
    id: int
    mode: str
    status: str
    phase: str
    degraded: bool
    tier: str | None
    budget: dict[str, Any] | None
    coverage: Decimal | None
    requests_made: int
    warnings: list[str]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None


class RunListResponse(BaseModel):
    data: list[RunSummary]
    total: int
    limit: int
    offset: int


class TaskResponse(BaseModel):
    id: int
    brand_id: int | None
    index_type: str
    status: str
    attempts: int
    hits_collected: int
    expected_hits: int | None
    coverage: Decimal | None
    tier: str | None
    error: str | None


class RunDetailResponse(BaseModel):
    run: RunSummary
    tasks: list[TaskResponse]


class ProgressResponse(BaseModel):
    status: str
    phase: str
    tier: str | None
    degraded: bool
    brands_total: int = 0
    brands_completed: int = 0
    tasks_total: int = 0
    tasks_done: int = 0
    hits_fetched: int = 0
    requests_made: int = 0
    coverage: Decimal | None = None
    partial: bool = False
    truncated: bool = False
    current_brand: str | None = None
    tasks_failed: int = 0
    eta_seconds: int | None = None
    heartbeat_at: datetime | None
    warnings: list[str]
    errors: list[dict[str, Any]] = []


class ParserHealthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["ready", "degraded", "unavailable"]
    source_mode: str
    transports: dict[str, bool]
    discovery: dict[str, Any]
    schema_status: dict[str, Any] = Field(serialization_alias="schema")
    active_runs: list[int]
    reasons: list[str] = []
    versions: dict[str, str | None] = {}
    circuits: list[dict[str, Any]] = []
    compliance: dict[str, Any] = {}
    last_run: dict[str, Any] | None = None
    runtime: dict[str, Any] = {}


class RunMetricsResponse(BaseModel):
    requests_total: int = 0
    requests_by_tier: dict[str, int] = {}
    http_errors_by_code: dict[str, int] = {}
    retries: int = 0
    rate_limit_hits: int = 0
    avg_latency_ms: float = 0
    p95_latency_ms: float = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_hit_rate: float = 0
    hits_fetched: int = 0
    listings_inserted: int = 0
    listings_updated: int = 0
    listings_invalid: int = 0
    quality_flags_counts: dict[str, int] = {}
    coverage_by_brand: dict[str, str | None] = {}
    duration_s: float = 0


def get_parser_runtime(request: Request) -> ParserRuntime:
    runtime = getattr(request.app.state, "parser_runtime", None)
    if not isinstance(runtime, ParserRuntime):
        raise ApiError(503, "parser_unavailable", "Parser runtime is unavailable")
    return runtime


@router.post("/run", status_code=202)
async def start_run(
    payload: RunRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
    runtime: Annotated[ParserRuntime, Depends(get_parser_runtime)],
) -> dict[str, Any]:
    try:
        require_live_compliance(settings)
    except RuntimeError as exc:
        raise ApiError(503, str(exc), "Live mode requires compliance acknowledgement") from exc
    if runtime.active_run_ids() or await session.scalar(
        select(ParserRun.id).where(ParserRun.status.in_(("pending", "running"))).limit(1)
    ):
        raise ApiError(409, "run_active", "A collection run is already in progress")
    await ensure_discovery(session, settings)
    try:
        plan = await ParserPlanner(session, settings).build(brand_ids=payload.brand_ids)
    except LookupError as exc:
        raise ApiError(404, "brand_not_found", "One or more brands do not exist") from exc
    except RuntimeError as exc:
        code = str(exc)
        status = 409 if code in {"brand_mapping_required", "brands_required"} else 503
        raise ApiError(status, code, "Parser prerequisites are incomplete") from exc
    run = await RunRepository(session).create(
        mode=plan.mode,
        budget=plan.budget,
        tasks=[item.persisted() for item in plan.tasks],
        warnings=plan.warnings,
    )
    await session.commit()
    if isinstance(runtime, ParserRuntime):
        runtime.start(run.id, settings=settings)
    else:
        runtime.start(run.id)
    return {"run": _summary(run).model_dump(mode="json")}


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: RunStatus | None = None,
) -> RunListResponse:
    rows, total = await RunRepository(session).list(limit=limit, offset=offset, status=status)
    return RunListResponse(
        data=[_summary(item) for item in rows], total=total, limit=limit, offset=offset
    )


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
async def run_detail(
    run_id: int, session: Annotated[AsyncSession, Depends(get_db)]
) -> RunDetailResponse:
    repository = RunRepository(session)
    run = await repository.get(run_id)
    if run is None:
        raise ApiError(404, "run_not_found", "Parser run does not exist")
    return RunDetailResponse(
        run=_summary(run), tasks=[_task(item) for item in await repository.tasks(run_id)]
    )


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    runtime: Annotated[ParserRuntime, Depends(get_parser_runtime)],
) -> Response:
    run = await RunRepository(session).get(run_id)
    if run is None:
        raise ApiError(404, "run_not_found", "Parser run does not exist")
    if run.status in {"pending", "running"} or run_id in runtime.active_run_ids():
        raise ApiError(409, "run_active", "An active parser run cannot be deleted")
    await session.delete(run)
    await session.commit()
    return Response(status_code=204)


@router.post("/data/clear", response_model=ClearDataResponse)
async def clear_collected_data(
    payload: ClearDataRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    runtime: Annotated[ParserRuntime, Depends(get_parser_runtime)],
) -> ClearDataResponse:
    del payload
    await _ensure_no_active_runs(session, runtime)
    listings_deleted = int(await session.scalar(select(func.count(Listing.id))) or 0)
    runs_deleted = int(await session.scalar(select(func.count(ParserRun.id))) or 0)
    await session.execute(delete(GroupMetric))
    await session.execute(delete(Listing))
    await session.execute(delete(ParserRun))
    await session.execute(delete(ParserWatermark))
    await session.execute(delete(UnmatchedBrand))
    # Seeds, user groups and "not a model" rules are kept; derived groups are rebuilt.
    await session.execute(delete(ModelGroup).where(ModelGroup.source.in_(("mined", "system"))))
    await session.execute(update(Brand).values(grouping_hash=None, grouped_at=None))
    await session.commit()
    return ClearDataResponse(
        listings_deleted=listings_deleted,
        runs_deleted=runs_deleted,
    )


@router.post("/history/clear", response_model=ClearHistoryResponse)
async def clear_run_history(
    payload: ClearDataRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    runtime: Annotated[ParserRuntime, Depends(get_parser_runtime)],
) -> ClearHistoryResponse:
    del payload
    await _ensure_no_active_runs(session, runtime)
    runs_deleted = int(await session.scalar(select(func.count(ParserRun.id))) or 0)
    await session.execute(delete(ParserRun))
    await session.commit()
    return ClearHistoryResponse(runs_deleted=runs_deleted)


async def _ensure_no_active_runs(session: AsyncSession, runtime: ParserRuntime) -> None:
    if runtime.active_run_ids() or await session.scalar(
        select(ParserRun.id).where(ParserRun.status.in_(("pending", "running"))).limit(1)
    ):
        raise ApiError(409, "run_active", "Stop active parser runs before deleting data")


@router.get("/runs/{run_id}/progress", response_model=ProgressResponse)
async def run_progress(
    run_id: int, session: Annotated[AsyncSession, Depends(get_db)]
) -> ProgressResponse:
    run = await RunRepository(session).get(run_id)
    if run is None:
        raise ApiError(404, "run_not_found", "Parser run does not exist")
    tasks = await RunRepository(session).tasks(run_id)
    progress = dict(run.stats.get("progress", {}))
    fetching = dict(run.stats.get("fetching", {}))
    running_task = next((item for item in tasks if item.status == "running"), None)
    errors = [
        {
            "task_id": item.id,
            "brand_id": item.brand_id,
            "index_type": item.index_type,
            "code": item.error,
        }
        for item in tasks
        if item.status == "failed" and item.error
    ]
    return ProgressResponse(
        status=run.status,
        phase=run.phase,
        tier=run.tier_used,
        degraded=run.degraded_mode,
        brands_total=int(progress.get("brands_total", 0)),
        brands_completed=int(progress.get("brands_completed", 0)),
        tasks_total=int(progress.get("tasks_total", 0)),
        tasks_done=int(progress.get("tasks_done", 0)),
        hits_fetched=int(progress.get("hits_fetched", 0)),
        requests_made=run.requests_made,
        coverage=run.coverage_avg,
        partial=run.status == "partial"
        or bool(fetching.get("partial"))
        or bool(fetching.get("poor")),
        truncated=bool(fetching.get("truncated"))
        or any(item.status == "truncated" for item in tasks),
        current_brand=(
            str((running_task.bucket_spec or {}).get("brand_name"))
            if running_task is not None
            else None
        ),
        tasks_failed=sum(item.status == "failed" for item in tasks),
        heartbeat_at=run.heartbeat_at,
        warnings=list(run.warnings),
        errors=errors,
    )


@router.get("/runs/{run_id}/report")
async def run_report(
    run_id: int, session: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    repository = RunRepository(session)
    run = await repository.get(run_id)
    if run is None:
        raise ApiError(404, "run_not_found", "Parser run does not exist")
    tasks = await repository.tasks(run_id)
    return {
        "run": _summary(run).model_dump(mode="json"),
        "stats": run.stats,
        "metrics": RunMetricsResponse.model_validate(
            run.stats.get("observability", {})
        ).model_dump(),
        "coverage_by_brand": {
            str(key): str(value) if value is not None else None
            for key, value in (await repository.coverage_by_brand(run_id)).items()
        },
        "tasks": [_task(item).model_dump(mode="json") for item in tasks],
    }


@router.post("/runs/{run_id}/cancel", status_code=202, response_model=RunSummary)
async def cancel_run(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    runtime: Annotated[ParserRuntime, Depends(get_parser_runtime)],
) -> RunSummary:
    repository = RunRepository(session)
    run = await repository.get(run_id)
    if run is None:
        raise ApiError(404, "run_not_found", "Parser run does not exist")
    if run.status not in {"pending", "running"}:
        raise ApiError(409, "run_not_cancellable", "Parser run is already terminal")
    if not runtime.cancel(run_id):
        await repository.finish(run_id, "cancelled")
        await session.commit()
    return _summary(run)


@router.post("/runs/{run_id}/resume", status_code=202, response_model=RunSummary)
async def resume_run(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
    runtime: Annotated[ParserRuntime, Depends(get_parser_runtime)],
) -> RunSummary:
    try:
        require_live_compliance(settings)
    except RuntimeError as exc:
        raise ApiError(503, str(exc), "Live mode requires compliance acknowledgement") from exc
    repository = RunRepository(session)
    existing = await repository.get(run_id)
    if existing is None:
        raise ApiError(404, "run_not_found", "Parser run does not exist")
    await ensure_discovery(session, settings)
    try:
        run = await repository.prepare_resume(run_id)
    except LookupError as exc:
        raise ApiError(404, "run_not_found", "Parser run does not exist") from exc
    except ValueError as exc:
        raise ApiError(409, "run_not_resumable", "Parser run cannot be resumed") from exc
    await session.commit()
    if isinstance(runtime, ParserRuntime):
        runtime.start(run_id, settings=settings)
    else:
        runtime.start(run_id)
    return _summary(run)


@router.get("/health", response_model=ParserHealthResponse)
async def parser_health(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
    runtime: Annotated[ParserRuntime, Depends(get_parser_runtime)],
) -> ParserHealthResponse:
    credential = await session.scalar(
        select(SourceCredential).where(SourceCredential.source == "grailed")
    )
    schema = await session.scalar(
        select(SourceSchema)
        .where(SourceSchema.source == "grailed")
        .order_by(SourceSchema.detected_at.desc())
        .limit(1)
    )
    alerts = int(
        await session.scalar(
            select(func.count(SchemaAlert.id)).where(SchemaAlert.resolved_at.is_(None))
        )
        or 0
    )
    alert_rows = list(
        await session.scalars(
            select(SchemaAlert)
            .where(SchemaAlert.resolved_at.is_(None))
            .order_by(SchemaAlert.created_at.desc())
            .limit(20)
        )
    )
    last_run = await session.scalar(select(ParserRun).order_by(ParserRun.id.desc()).limit(1))
    brands = list(await session.scalars(select(Brand).options(selectinload(Brand.source_mappings))))
    health_snapshot = getattr(runtime, "health_snapshot", None)
    runtime_health = (
        health_snapshot()
        if callable(health_snapshot)
        else {"circuits": [], "metrics": {}, "tier": None}
    )
    reasons = compliance_reasons(settings)
    unavailable_reasons = {
        "live_compliance_not_acknowledged",
        "credentials_missing",
        "schema_missing",
    }
    if credential is None:
        reasons.append("credentials_missing")
    if schema is None:
        reasons.append("schema_missing")
    if not brands or any(not _verified_mapping(brand) for brand in brands):
        reasons.append("brand_mapping_required")
    now = datetime.now(UTC)
    if credential is not None:
        discovered_at = credential.discovered_at
        if discovered_at.tzinfo is None:
            discovered_at = discovered_at.replace(tzinfo=UTC)
        stale_at = discovered_at + timedelta(hours=settings.discovery_ttl_hours)
        if credential.valid_until is not None:
            valid_until = credential.valid_until
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=UTC)
            stale_at = min(stale_at, valid_until)
        if now >= stale_at or credential.verification_status == "stale":
            reasons.append("credentials_stale")
    if alerts:
        reasons.append("schema_drift_active")
    open_circuits = [
        item for item in runtime_health.get("circuits", []) if item.get("state") != "closed"
    ]
    if open_circuits:
        reasons.append("circuit_open")
    if last_run is not None and last_run.degraded_mode:
        reasons.append("last_run_degraded")
    reasons = list(dict.fromkeys(reasons))
    if any(reason in unavailable_reasons for reason in reasons):
        health_status: Literal["ready", "degraded", "unavailable"] = "unavailable"
    elif reasons:
        health_status = "degraded"
    else:
        health_status = "ready"
    response.status_code = 503 if health_status == "unavailable" else 200
    return ParserHealthResponse(
        status=health_status,
        source_mode=settings.source_mode,
        transports={"T1": True},
        discovery={
            "available": credential is not None,
            "status": credential.verification_status if credential else "missing",
            "discovered_at": credential.discovered_at.isoformat() if credential else None,
            "valid_until": credential.valid_until.isoformat()
            if credential and credential.valid_until
            else None,
        },
        schema_status={
            "detected_at": schema.detected_at.isoformat() if schema else None,
            "drift_score": str(schema.drift_score)
            if schema and schema.drift_score is not None
            else None,
            "active_alerts": alerts,
            "alerts": [
                {
                    "id": item.id,
                    "severity": item.severity,
                    "message": item.message,
                    "details": item.details,
                    "created_at": item.created_at.isoformat(),
                }
                for item in alert_rows
            ],
        },
        active_runs=runtime.active_run_ids(),
        reasons=reasons,
        versions={"curl_cffi": _package_version("curl_cffi")},
        circuits=list(runtime_health.get("circuits", [])),
        compliance={
            "live_acknowledged": settings.live_compliance_acknowledged,
            "seller_identity_mode": settings.store_seller_identity,
            "limits": {
                "requests_per_minute": settings.requests_per_minute,
                "max_concurrency": settings.max_concurrent_requests,
            },
        },
        last_run=(
            {
                "id": last_run.id,
                "status": last_run.status,
                "degraded": last_run.degraded_mode,
                "tier": last_run.tier_used,
                "metrics": last_run.stats.get("observability", {}),
            }
            if last_run is not None
            else None
        ),
        runtime=getattr(request.app.state, "runtime_report", {}),
    )


@router.post("/discovery/refresh", response_model=DiscoveryResponse)
async def parser_discovery_refresh(
    payload: DiscoveryRefreshRequest,
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> DiscoveryResponse:
    try:
        require_live_compliance(settings)
    except RuntimeError as exc:
        raise ApiError(503, str(exc), "Live mode requires compliance acknowledgement") from exc
    return await refresh_discovery(payload, service, settings)


def _summary(run: ParserRun) -> RunSummary:
    return RunSummary(
        id=run.id,
        mode=run.mode,
        status=run.status,
        phase=run.phase,
        degraded=run.degraded_mode,
        tier=run.tier_used,
        budget=run.budget_estimate,
        coverage=run.coverage_avg,
        requests_made=run.requests_made,
        warnings=list(run.warnings),
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        heartbeat_at=run.heartbeat_at,
    )


def _task(task: ParserRunTask) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        brand_id=task.brand_id,
        index_type=task.index_type,
        status=task.status,
        attempts=task.attempts,
        hits_collected=task.hits_collected,
        expected_hits=task.expected_hits,
        coverage=task.coverage,
        tier=task.fetch_tier,
        error=task.error if task.status == "failed" else None,
    )


def _verified_mapping(brand: Brand) -> bool:
    return any(
        item.verified
        and item.rejected_at is None
        and (brand.include_subbrands or not item.is_subbrand)
        for item in brand.source_mappings
    )


def _package_version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None
