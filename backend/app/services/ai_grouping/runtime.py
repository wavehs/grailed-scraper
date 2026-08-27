"""Process-local lifecycle for persisted AI grouping runs."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import AiGroupingBatch, AiGroupingRun
from app.services.ai_grouping.client import GeminiBatchClient
from app.services.ai_grouping.service import AiGroupingService, GroupingMode

_RECONCILABLE = {
    "preparing",
    "submitted",
    "running",
    "validating",
    "waiting_for_market",
    "applying",
    "interrupted",
}
_CANCELLABLE = _RECONCILABLE | {"failed", "needs_attention"}
_RESUMABLE = {"failed", "cancelled", "interrupted", "needs_attention"}
_ADMISSION_STATES = _RECONCILABLE | {"needs_attention"}
_ADMISSION_BATCH_STATES = {"preparing", "submitted", "running", "interrupted", "needs_attention"}
_SAFE_ERROR = re.compile(r"[a-z0-9_]{1,80}")


class AiGroupingRuntime:
    """Run persisted Gemini jobs without making application startup submit new work."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        market_lock: asyncio.Lock | None = None,
        service: Any | None = None,
        client: Any | None = None,
    ) -> None:
        self._sessions = sessions
        self._service = service or AiGroupingService(sessions, settings)
        self._market_lock = market_lock or asyncio.Lock()
        key = settings.gemini_api_key
        secret = key.get_secret_value().strip() if key is not None else ""
        self._client = client or (GeminiBatchClient(secret) if secret else None)
        self._jobs: dict[int, asyncio.Task[None]] = {}
        self._cancel: dict[int, asyncio.Event] = {}
        self._start_lock = asyncio.Lock()
        self._closed = False

    async def preflight(self, mode: GroupingMode) -> dict[str, Any]:
        return await self._service.preflight(mode)

    async def create_and_start(self, mode: GroupingMode, budget: Decimal) -> int:
        async with self._start_lock:
            self._require_client()
            run_id = await self._service.create_run(mode, budget)
            self.start(run_id)
            return run_id

    def start(self, run_id: int) -> None:
        self._require_client()
        if self._closed:
            raise RuntimeError("ai_grouping_runtime_closed")
        active = self._jobs.get(run_id)
        if active is not None and not active.done():
            raise RuntimeError("grouping_run_active")
        if any(other_id != run_id and not task.done() for other_id, task in self._jobs.items()):
            raise RuntimeError("grouping_run_active")
        cancelled = asyncio.Event()
        self._cancel[run_id] = cancelled
        task = asyncio.create_task(
            self._execute(run_id, cancelled), name=f"ai-grouping-run-{run_id}"
        )
        self._jobs[run_id] = task
        task.add_done_callback(lambda finished: self._forget(run_id, finished))

    async def reconcile(self) -> None:
        """Continue only runs already persisted before this process started."""

        if self._client is None:
            return
        async with self._sessions() as session:
            run_ids = list(
                await session.scalars(
                    select(AiGroupingRun.id)
                    .where(AiGroupingRun.status.in_(_RECONCILABLE))
                    .order_by(AiGroupingRun.id)
                )
            )
        for run_id in run_ids:
            self.start(run_id)

    async def cancel(self, run_id: int) -> None:
        client = self._require_client()
        run = await self._require_run(run_id)
        if run.status not in _CANCELLABLE:
            raise RuntimeError("grouping_run_not_cancellable")
        event = self._cancel.get(run_id)
        if event is not None:
            event.set()
        task = self._jobs.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._service.cancel_provider_work(run_id, client)

    async def resume(self, run_id: int) -> None:
        async with self._start_lock:
            self._require_client()
            if any(not task.done() for task in self._jobs.values()):
                raise RuntimeError("grouping_run_active")
            async with self._sessions() as session:
                current = await session.get(AiGroupingRun, run_id)
                if current is None:
                    raise LookupError("grouping_run_not_found")
                if current.status not in _RESUMABLE:
                    raise RuntimeError("grouping_run_not_resumable")
                active = await session.scalar(
                    select(AiGroupingRun.id)
                    .where(
                        AiGroupingRun.id != run_id,
                        AiGroupingRun.status.in_(_ADMISSION_STATES),
                    )
                    .limit(1)
                )
                active_batch = await session.scalar(
                    select(AiGroupingBatch.id)
                    .where(
                        AiGroupingBatch.run_id != run_id,
                        AiGroupingBatch.status.in_(_ADMISSION_BATCH_STATES),
                    )
                    .limit(1)
                )
                if active is not None or active_batch is not None:
                    raise RuntimeError("grouping_run_active")
                if current.status == "needs_attention":
                    await session.execute(
                        update(AiGroupingBatch)
                        .where(
                            AiGroupingBatch.run_id == run_id,
                            AiGroupingBatch.status == "needs_attention",
                            AiGroupingBatch.provider_job_name.is_(None),
                        )
                        .values(status="preparing", error=None, updated_at=datetime.now(UTC))
                    )
                    await session.execute(
                        update(AiGroupingBatch)
                        .where(
                            AiGroupingBatch.run_id == run_id,
                            AiGroupingBatch.status == "needs_attention",
                            AiGroupingBatch.provider_job_name.is_not(None),
                        )
                        .values(status="interrupted", error=None, updated_at=datetime.now(UTC))
                    )
                current.status = "interrupted"
                current.error = None
                current.finished_at = None
                current.heartbeat_at = datetime.now(UTC)
                await session.commit()
            self.start(run_id)

    async def rollback(self, run_id: int) -> None:
        await self._require_run(run_id)
        await self._service.rollback_run(run_id, market_lock=self._market_lock)

    def active_run_ids(self) -> list[int]:
        return sorted(run_id for run_id, task in self._jobs.items() if not task.done())

    async def close(self) -> None:
        self._closed = True
        pending = [task for task in self._jobs.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._client is not None:
            await self._client.close()

    async def _execute(self, run_id: int, cancelled: asyncio.Event) -> None:
        client = self._require_client()
        try:
            await self._service.process(
                run_id,
                client,
                market_lock=self._market_lock,
                cancelled=cancelled,
            )
        except asyncio.CancelledError:
            if not cancelled.is_set():
                await self._mark(run_id, "interrupted", "process_interrupted")
            raise
        except Exception as exc:
            await self._mark(run_id, "failed", _error_code(exc))

    async def _require_run(self, run_id: int) -> AiGroupingRun:
        async with self._sessions() as session:
            run = await session.get(AiGroupingRun, run_id)
            if run is None:
                raise LookupError("grouping_run_not_found")
            return run

    async def _mark(self, run_id: int, status: str, error: str) -> None:
        async with self._sessions() as session:
            run = await session.get(AiGroupingRun, run_id)
            if run is not None and run.status not in {
                "completed",
                "rolled_back",
                "cancelled",
                "needs_attention",
            }:
                now = datetime.now(UTC)
                uncertain = await session.scalar(
                    select(AiGroupingBatch.id)
                    .where(
                        AiGroupingBatch.run_id == run_id,
                        AiGroupingBatch.provider_job_name.is_(None),
                        AiGroupingBatch.status.in_(("preparing", "submitted")),
                    )
                    .limit(1)
                )
                run.status = "needs_attention" if uncertain is not None else status
                run.error = "provider_submission_uncertain" if uncertain is not None else error
                run.finished_at = now
                run.heartbeat_at = now
                if uncertain is not None:
                    await session.execute(
                        update(AiGroupingBatch)
                        .where(AiGroupingBatch.id == uncertain)
                        .values(
                            status="needs_attention",
                            error="provider_submission_uncertain",
                            updated_at=now,
                        )
                    )
                await session.commit()

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("gemini_not_configured")
        return self._client

    def _forget(self, run_id: int, task: asyncio.Task[None]) -> None:
        self._jobs.pop(run_id, None)
        self._cancel.pop(run_id, None)
        if not task.cancelled():
            task.exception()


def _error_code(exc: Exception) -> str:
    value = str(exc).casefold()
    return value if _SAFE_ERROR.fullmatch(value) else "ai_grouping_failed"
