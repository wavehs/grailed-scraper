"""Create collection runs the same way for the API and the scheduler."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import ParserRun
from app.repositories.runs import RunRepository
from app.services.parser.planner import ParserPlanner
from app.services.sources.grailed.discovery.service import DiscoveryService
from app.services.transport.factory import create_http_transport

# A run started with its own rate keeps it on resume (``stats`` survives restarts).
RUN_RPM_KEY = "requests_per_minute"
_CONFLICTS = frozenset({"brand_mapping_required", "brands_required"})


class LaunchError(Exception):
    """A run cannot be planned; ``status`` is the HTTP status the API answers with."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


async def refresh_stale_discovery(session: AsyncSession, settings: Settings) -> None:
    """Reuse cached credentials; refresh them (one page GET plus probes) when stale."""

    transport = create_http_transport(settings)
    try:
        await DiscoveryService(session, settings, transport).refresh(force=False)
        await session.commit()
    finally:
        await transport.close()


async def create_run(
    session: AsyncSession,
    settings: Settings,
    *,
    brand_ids: Sequence[int] | None,
    full: bool,
    stats: dict[str, Any] | None = None,
) -> ParserRun:
    """Plan and commit a pending run; the caller starts it in the runtime."""

    try:
        plan = await ParserPlanner(session, settings).build(
            brand_ids=list(brand_ids) if brand_ids is not None else None, full=full
        )
    except LookupError as exc:
        raise LaunchError(404, "brand_not_found", "One or more brands do not exist") from exc
    except RuntimeError as exc:
        code = str(exc)
        status = 409 if code in _CONFLICTS else 503
        raise LaunchError(status, code, "Parser prerequisites are incomplete") from exc
    run = await RunRepository(session).create(
        mode=plan.mode,
        budget=plan.budget,
        tasks=[item.persisted() for item in plan.tasks],
        warnings=plan.warnings,
    )
    if stats:
        run.stats = {**run.stats, **stats}
    await session.commit()
    return run


def run_settings(settings: Settings, run: ParserRun) -> Settings:
    """The settings a run executes with: its own stored rate, if it has one."""

    rpm = run.stats.get(RUN_RPM_KEY)
    if not isinstance(rpm, int) or rpm < 1:
        return settings
    return settings.model_copy(update={RUN_RPM_KEY: rpm})
