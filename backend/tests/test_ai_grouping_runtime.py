"""Runtime contracts for resumable AI grouping work."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import AiGroupingRun, Base
from app.services.ai_grouping.runtime import AiGroupingRuntime


class _Client:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Service:
    def __init__(self) -> None:
        self.processed: list[int] = []
        self.started = asyncio.Event()

    async def process(self, run_id: int, _client: Any, **_kwargs: Any) -> None:
        self.processed.append(run_id)
        self.started.set()


class _CreationService(_Service):
    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.created = 0

    async def create_run(self, _mode: str, _budget: Decimal) -> int:
        if self.active:
            raise RuntimeError("grouping_run_active")
        await asyncio.sleep(0)
        self.active = True
        self.created += 1
        return self.created


class _BlockingService(_Service):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def process(self, run_id: int, _client: Any, **_kwargs: Any) -> None:
        self.processed.append(run_id)
        self.started.set()
        await self.release.wait()


@pytest.mark.asyncio
async def test_runtime_reconciles_persisted_run_without_creating_another(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    async with sessions() as session:
        session.add(
            AiGroupingRun(
                mode="canary",
                status="interrupted",
                base_model="gemini-2.5-flash-lite",
                review_model="gemini-2.5-flash",
                grouping_version="grouping-v1",
                prompt_version="grouping-prompt-v1",
                budget_limit_usd=Decimal("0.50"),
                estimated_cost_usd=Decimal("0.10"),
                actual_cost_usd=Decimal(0),
                input_tokens=0,
                output_tokens=0,
                total_items=1,
                unique_requests=1,
                completed_items=0,
                ambiguous_items=0,
                failed_items=0,
                stats={},
                warnings=[],
                created_at=now,
                started_at=now,
                heartbeat_at=now,
            )
        )
        await session.commit()

    service = _Service()
    client = _Client()
    runtime = AiGroupingRuntime(
        sessions,
        Settings(gemini_api_key="test-key"),
        service=service,
        client=client,
    )
    await runtime.reconcile()
    await asyncio.wait_for(service.started.wait(), timeout=1)

    assert service.processed == [1]
    assert runtime.active_run_ids() in ([], [1])
    await runtime.close()
    assert client.closed is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_serializes_concurrent_run_creation(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'create.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = _CreationService()
    runtime = AiGroupingRuntime(
        sessions,
        Settings(gemini_api_key="test-key"),
        service=service,
        client=_Client(),
    )

    results = await asyncio.gather(
        runtime.create_and_start("canary", Decimal("0.50")),
        runtime.create_and_start("canary", Decimal("0.50")),
        return_exceptions=True,
    )

    assert service.created == 1
    assert sum(isinstance(value, RuntimeError) for value in results) == 1
    await runtime.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_refuses_resume_while_another_run_is_active(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'resume.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    async with sessions() as session:
        for status in ("failed", "cancelled"):
            session.add(
                AiGroupingRun(
                    mode="canary",
                    status=status,
                    base_model="gemini-2.5-flash-lite",
                    review_model="gemini-2.5-flash",
                    grouping_version="grouping-v1",
                    prompt_version="grouping-prompt-v1",
                    budget_limit_usd=Decimal("0.50"),
                    actual_cost_usd=Decimal(0),
                    input_tokens=0,
                    output_tokens=0,
                    total_items=0,
                    unique_requests=0,
                    completed_items=0,
                    ambiguous_items=0,
                    failed_items=0,
                    stats={},
                    warnings=[],
                    created_at=now,
                )
            )
        await session.commit()
    service = _BlockingService()
    runtime = AiGroupingRuntime(
        sessions,
        Settings(gemini_api_key="test-key"),
        service=service,
        client=_Client(),
    )

    await runtime.resume(1)
    await asyncio.wait_for(service.started.wait(), timeout=1)
    with pytest.raises(RuntimeError, match="grouping_run_active"):
        await runtime.resume(2)

    service.release.set()
    await runtime.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_refuses_to_cancel_completed_run(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'terminal.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    async with sessions() as session:
        session.add(
            AiGroupingRun(
                mode="canary",
                status="completed",
                base_model="gemini-2.5-flash-lite",
                review_model="gemini-2.5-flash",
                grouping_version="grouping-v1",
                prompt_version="grouping-prompt-v1",
                budget_limit_usd=Decimal("0.50"),
                actual_cost_usd=Decimal(0),
                input_tokens=0,
                output_tokens=0,
                total_items=0,
                unique_requests=0,
                completed_items=0,
                ambiguous_items=0,
                failed_items=0,
                stats={},
                warnings=[],
                created_at=now,
            )
        )
        await session.commit()
    runtime = AiGroupingRuntime(
        sessions,
        Settings(gemini_api_key="test-key"),
        service=_Service(),
        client=_Client(),
    )

    with pytest.raises(RuntimeError, match="grouping_run_not_cancellable"):
        await runtime.cancel(1)

    async with sessions() as session:
        run = await session.get(AiGroupingRun, 1)
        assert run is not None and run.status == "completed"
    await runtime.close()
    await engine.dispose()
