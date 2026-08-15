"""Durable parser-run state and checkpoint persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ParserRun, ParserRunTask

TERMINAL_RUN_STATUSES = {"completed", "partial", "failed", "cancelled"}


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        mode: str,
        budget: dict[str, Any],
        tasks: Sequence[dict[str, Any]],
        warnings: Sequence[str] = (),
    ) -> ParserRun:
        now = datetime.now(UTC)
        run = ParserRun(
            source="grailed",
            mode=mode,
            status="pending",
            phase="planning",
            dry_run=False,
            degraded_mode=False,
            budget_estimate=budget,
            requests_made=0,
            warnings=list(warnings),
            stats={"progress": {}},
            created_at=now,
            heartbeat_at=now,
        )
        self._session.add(run)
        await self._session.flush()
        for item in tasks:
            self._session.add(
                ParserRunTask(
                    run_id=run.id,
                    brand_id=item.get("brand_id"),
                    index_type=str(item["index_type"]),
                    bucket_spec=dict(item["bucket_spec"]),
                    status=str(item.get("status", "pending")),
                    attempts=0,
                    hits_collected=0,
                    error=item.get("error"),
                )
            )
        await self._session.flush()
        return run

    async def get(self, run_id: int) -> ParserRun | None:
        return await self._session.get(ParserRun, run_id)

    async def tasks(self, run_id: int) -> list[ParserRunTask]:
        return list(
            await self._session.scalars(
                select(ParserRunTask)
                .where(ParserRunTask.run_id == run_id)
                .order_by(ParserRunTask.id)
            )
        )

    async def list(
        self, *, limit: int, offset: int, status: str | None = None
    ) -> tuple[list[ParserRun], int]:
        statement = select(ParserRun)
        count = select(func.count(ParserRun.id))
        if status is not None:
            statement = statement.where(ParserRun.status == status)
            count = count.where(ParserRun.status == status)
        rows = list(
            await self._session.scalars(
                statement.order_by(ParserRun.id.desc()).limit(limit).offset(offset)
            )
        )
        return rows, int(await self._session.scalar(count) or 0)

    async def begin(self, run_id: int) -> ParserRun:
        run = await self._required(run_id)
        now = datetime.now(UTC)
        run.status = "running"
        run.phase = "fetching"
        run.started_at = run.started_at or now
        run.finished_at = None
        run.heartbeat_at = now
        await self._session.flush()
        return run

    async def set_phase(self, run_id: int, phase: str) -> None:
        run = await self._required(run_id)
        run.phase = phase
        run.heartbeat_at = datetime.now(UTC)
        await self._session.flush()

    async def heartbeat(self, run_id: int) -> None:
        run = await self._required(run_id)
        tasks = await self.tasks(run_id)
        done = sum(item.status in {"done", "skipped", "truncated"} for item in tasks)
        brand_ids = {item.brand_id for item in tasks if item.brand_id is not None}
        completed_brand_ids = {
            brand_id
            for brand_id in brand_ids
            if all(
                item.status in {"done", "skipped", "truncated"}
                for item in tasks
                if item.brand_id == brand_id
            )
        }
        now = datetime.now(UTC)
        progress = {
            "phase": run.phase,
            "brands_total": len(brand_ids),
            "brands_completed": len(completed_brand_ids),
            "tasks_total": len(tasks),
            "tasks_done": done,
            "hits_fetched": sum(item.hits_collected for item in tasks),
            "updated_at": now.isoformat(),
        }
        run.stats = {**run.stats, "progress": progress}
        run.heartbeat_at = now
        await self._session.flush()

    async def finish(self, run_id: int, status: str) -> ParserRun:
        run = await self._required(run_id)
        run.status = status
        run.phase = "done"
        run.finished_at = datetime.now(UTC)
        run.heartbeat_at = run.finished_at
        await self._session.flush()
        return run

    async def prepare_resume(self, run_id: int) -> ParserRun:
        run = await self._required(run_id)
        if run.status not in {"interrupted", "failed", "partial", "cancelled"}:
            raise ValueError("run_not_resumable")
        await self._session.execute(
            update(ParserRunTask)
            .where(
                ParserRunTask.run_id == run_id,
                ParserRunTask.status.in_(("running", "failed", "truncated")),
            )
            .values(status="pending", error=None, finished_at=None)
        )
        run.status = "pending"
        run.phase = "fetching"
        run.finished_at = None
        run.heartbeat_at = datetime.now(UTC)
        await self._session.flush()
        return run

    async def reconcile_interrupted(self) -> int:
        run_ids = list(
            await self._session.scalars(select(ParserRun.id).where(ParserRun.status == "running"))
        )
        if not run_ids:
            return 0
        now = datetime.now(UTC)
        await self._session.execute(
            update(ParserRun)
            .where(ParserRun.id.in_(run_ids))
            .values(status="interrupted", phase="done", finished_at=now, heartbeat_at=now)
        )
        await self._session.execute(
            update(ParserRunTask)
            .where(ParserRunTask.run_id.in_(run_ids), ParserRunTask.status == "running")
            .values(status="pending")
        )
        return len(run_ids)

    async def aggregate_status(self, run_id: int) -> str:
        tasks = await self.tasks(run_id)
        failed = sum(item.status == "failed" for item in tasks)
        truncated = sum(item.status == "truncated" for item in tasks)
        successful = sum(item.status in {"done", "skipped"} for item in tasks)
        if failed and not (successful or truncated):
            return "failed"
        if failed or truncated:
            return "partial"
        return "completed"

    async def coverage_by_brand(self, run_id: int) -> dict[int, Decimal | None]:
        tasks = await self.tasks(run_id)
        grouped: dict[int, list[Decimal]] = {}
        for item in tasks:
            if item.brand_id is not None and item.coverage is not None:
                grouped.setdefault(item.brand_id, []).append(item.coverage)
        return {
            brand_id: sum(values, Decimal(0)) / Decimal(len(values)) if values else None
            for brand_id, values in grouped.items()
        }

    async def _required(self, run_id: int) -> ParserRun:
        run = await self.get(run_id)
        if run is None:
            raise LookupError(f"Parser run {run_id} does not exist")
        return run
