"""State of the in-app schedule and the pause it sets after a throttled run."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mutation import MutationRoute
from app.api.parser import get_parser_runtime
from app.api.settings import get_effective_settings
from app.core.config import Settings
from app.db.models import ParserRun
from app.db.session import get_db
from app.services.parser.runtime import ParserRuntime
from app.services.scheduler import (
    FULL_WEEKDAY,
    SCHEDULE_KEY,
    Action,
    Scheduler,
    clear_pause,
    read_state,
)

router = APIRouter(prefix="/parser/schedule", tags=["parser"], route_class=MutationRoute)


class ScheduledRun(BaseModel):
    id: int
    status: str
    slot: str
    full: bool
    attempts: int
    stop_reason: str | None
    maintenance_error: str | None
    created_at: datetime
    finished_at: datetime | None


class ScheduleResponse(BaseModel):
    enabled: bool
    hour: int
    rpm: int
    brand_ids: list[int] | None
    full_weekday: int
    state: Action
    reason: str
    slot: str
    next_slot_at: datetime
    paused_until: datetime | None
    last_run: ScheduledRun | None


@router.get("", response_model=ScheduleResponse)
async def read_schedule(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
    runtime: Annotated[ParserRuntime, Depends(get_parser_runtime)],
) -> ScheduleResponse:
    scheduler = getattr(request.app.state, "scheduler", None)
    state = await read_state(
        session,
        settings,
        now=datetime.now().astimezone(),
        active_run_ids=runtime.active_run_ids(),
        # The scheduler's failed launches, so the card shows a backoff or a give-up.
        launches=scheduler.launches if isinstance(scheduler, Scheduler) else None,
    )
    return ScheduleResponse(
        enabled=settings.schedule_enabled,
        hour=settings.schedule_hour,
        rpm=settings.schedule_rpm,
        brand_ids=settings.schedule_brand_ids,
        full_weekday=FULL_WEEKDAY,
        state=state.decision.action,
        reason=state.decision.reason,
        slot=state.decision.key,
        next_slot_at=state.next_slot_at,
        paused_until=state.paused_until,
        last_run=_scheduled(state.last_run),
    )


@router.delete("/pause", status_code=204)
async def clear_schedule_pause(
    request: Request, session: Annotated[AsyncSession, Depends(get_db)]
) -> Response:
    await clear_pause(session)
    await session.commit()
    scheduler = getattr(request.app.state, "scheduler", None)
    if isinstance(scheduler, Scheduler):
        scheduler.wake()
    return Response(status_code=204)


def _scheduled(run: ParserRun | None) -> ScheduledRun | None:
    if run is None:
        return None
    schedule = run.stats.get(SCHEDULE_KEY, {})
    stop_reason = run.stats.get("stop_reason")
    after = schedule.get("after")
    error = after.get("error") if isinstance(after, dict) else None
    return ScheduledRun(
        id=run.id,
        status=run.status,
        slot=str(schedule.get("slot", "")),
        full=bool(schedule.get("full", False)),
        attempts=int(schedule.get("attempts", 1)),
        stop_reason=stop_reason if isinstance(stop_reason, str) else None,
        maintenance_error=error if isinstance(error, str) else None,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )
