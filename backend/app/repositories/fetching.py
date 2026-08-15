"""Persist stage-six coverage without coupling it to the future orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ParserRun, ParserRunTask
from app.domain.listings import FetchTier
from app.services.sources.base.models import CoverageReport

_TIER_ORDER = {"T1": 1, "T2": 2, "T3": 3}


class FetchReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def finish_task(
        self,
        task_id: int,
        report: CoverageReport,
        *,
        fetch_tier: FetchTier,
        cursor: str | None,
        now: datetime | None = None,
    ) -> ParserRunTask:
        task = await self._session.get(ParserRunTask, task_id)
        if task is None:
            raise LookupError(f"Parser run task {task_id} does not exist")
        task.hits_collected = report.collected_hits
        task.expected_hits = report.expected_hits
        task.coverage = report.coverage
        task.cursor = cursor
        task.fetch_tier = fetch_tier
        task.status = (
            "truncated"
            if report.truncated
            else ("skipped" if report.status == "skipped" else "done")
        )
        task.finished_at = now or datetime.now(UTC)
        if report.warnings:
            task.error = "; ".join(report.warnings)
        await self._session.flush()
        await self._update_run(task.run_id)
        return task

    async def _update_run(self, run_id: int) -> None:
        parser_run = await self._session.get(ParserRun, run_id)
        if parser_run is None:
            raise LookupError(f"Parser run {run_id} does not exist")
        tasks = tuple(
            await self._session.scalars(select(ParserRunTask).where(ParserRunTask.run_id == run_id))
        )
        coverages = [task.coverage for task in tasks if task.coverage is not None]
        parser_run.coverage_avg = (
            sum(coverages, Decimal(0)) / Decimal(len(coverages)) if coverages else None
        )
        tiers = [task.fetch_tier for task in tasks if task.fetch_tier is not None]
        if tiers:
            parser_run.tier_used = max(tiers, key=lambda tier: _TIER_ORDER[tier])
        parser_run.degraded_mode = any(tier in {"T2", "T3"} for tier in tiers)
        warnings = list(parser_run.warnings)
        for task in tasks:
            if task.error:
                warning = f"task {task.id}: {task.error}"
                if warning not in warnings:
                    warnings.append(warning)
        parser_run.warnings = warnings
        parser_run.stats = {
            **parser_run.stats,
            "fetching": {
                "tasks": len(tasks),
                "partial": sum(
                    task.coverage is not None and Decimal("0.70") <= task.coverage < Decimal("0.98")
                    for task in tasks
                ),
                "poor": sum(
                    task.coverage is not None and task.coverage < Decimal("0.70") for task in tasks
                ),
                "truncated": sum(task.status == "truncated" for task in tasks),
            },
        }
        await self._session.flush()
