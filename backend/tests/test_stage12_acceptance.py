"""Offline release-candidate acceptance scenarios for stage 12."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import (
    Base,
    Brand,
    BrandSourceMap,
    Listing,
    ParserRun,
    ParserRunTask,
    ScoringSnapshot,
)
from app.repositories.runs import RunRepository
from app.services.parser.mock.generator import BRANDS
from app.services.parser.planner import ParserPlanner
from app.services.parser.runtime import ParserRuntime


async def _database(path: Path) -> tuple[object, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def _seed_brands(factory: async_sessionmaker[AsyncSession], count: int = 21) -> None:
    now = datetime.now(UTC)
    async with factory() as session:
        for definition in BRANDS[:count]:
            brand = Brand(
                name=definition.name,
                slug=definition.slug,
                aliases=list(definition.aliases),
                include_subbrands=False,
                created_at=now,
                updated_at=now,
            )
            session.add(brand)
            await session.flush()
            session.add(
                BrandSourceMap(
                    brand_id=brand.id,
                    source="grailed",
                    source_designer_name=definition.designer_name,
                    source_slug=definition.slug,
                    source_designer_id=None,
                    listings_count=400,
                    match_score=Decimal("1"),
                    match_method="manual",
                    verified=True,
                    is_subbrand=False,
                    rejected_at=None,
                    updated_at=now,
                )
            )
        await session.commit()


async def _create_run(
    factory: async_sessionmaker[AsyncSession], settings: Settings, *, mode: str = "full"
) -> int:
    async with factory() as session:
        plan = await ParserPlanner(session, settings).build(mode=mode)
        run = await RunRepository(session).create(
            mode=mode,
            budget=plan.budget,
            tasks=[item.persisted() for item in plan.tasks],
            warnings=plan.warnings,
        )
        await session.commit()
        return run.id


async def _wait_for_terminal(
    factory: async_sessionmaker[AsyncSession], run_id: int, timeout_s: float = 90
) -> str:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        async with factory() as session:
            status = await session.scalar(select(ParserRun.status).where(ParserRun.id == run_id))
        if status in {"completed", "partial", "failed", "cancelled", "interrupted"}:
            assert isinstance(status, str)
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


async def test_full_mock_e2e_covers_all_21_brands_without_duplicates(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path / "stage12-e2e.db")
    await _seed_brands(factory)
    settings = Settings(source_mode="mock", parser_max_concurrency=3)
    run_id = await _create_run(factory, settings)
    runtime = ParserRuntime(factory, settings)
    runtime.start(run_id)
    assert await _wait_for_terminal(factory, run_id, timeout_s=120) == "completed"
    await runtime.close()

    async with factory() as session:
        run = await session.get(ParserRun, run_id)
        tasks = list(
            await session.scalars(
                select(ParserRunTask).where(ParserRunTask.run_id == run_id)
            )
        )
        listing_count = int(await session.scalar(select(func.count(Listing.id))) or 0)
        unique_count = int(
            await session.scalar(select(func.count(func.distinct(Listing.grailed_id)))) or 0
        )
        snapshot_count = int(
            await session.scalar(select(func.count(ScoringSnapshot.id))) or 0
        )
    assert run is not None
    assert len(tasks) == 42 and all(task.status == "done" for task in tasks)
    assert all(task.coverage is not None and task.coverage >= Decimal("0.98") for task in tasks)
    assert listing_count == unique_count == 8_400
    assert snapshot_count == 21 * 8
    assert run.stats["scoring"]["status"] == "completed"
    assert set(run.stats["observability"]["requests_by_tier"]) <= {"T0"}
    assert run.degraded_mode is False
    await engine.dispose()  # type: ignore[attr-defined]


async def test_cooperative_cancel_then_resume_completes_without_duplicates(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path / "stage12-cancel.db")
    await _seed_brands(factory, count=1)
    settings = Settings(source_mode="mock", parser_max_concurrency=1)
    run_id = await _create_run(factory, settings)
    runtime = ParserRuntime(factory, settings)
    runtime.start(run_id)
    assert runtime.cancel(run_id)
    assert await _wait_for_terminal(factory, run_id) == "cancelled"
    async with factory() as session:
        await RunRepository(session).prepare_resume(run_id)
        await session.commit()
    runtime.start(run_id)
    assert await _wait_for_terminal(factory, run_id) == "completed"
    await runtime.close()
    async with factory() as session:
        count = int(await session.scalar(select(func.count(Listing.id))) or 0)
        unique = int(
            await session.scalar(select(func.count(func.distinct(Listing.grailed_id)))) or 0
        )
    assert count == unique == 400
    await engine.dispose()  # type: ignore[attr-defined]


async def test_crashed_process_is_reconciled_and_resumed(tmp_path: Path) -> None:
    database = tmp_path / "stage12-crash.db"
    marker = tmp_path / "stage12-crash.ready"
    database_url = f"sqlite+aiosqlite:///{database}"
    helper = Path(__file__).parent / "helpers" / "stage12_crash_worker.py"
    result = subprocess.run(
        [sys.executable, str(helper), database_url, str(marker)],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
        check=False,
        timeout=30,
    )
    assert result.returncode == 23 and marker.read_text(encoding="ascii") == "ready"

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(source_mode="mock", parser_max_concurrency=1)
    runtime = ParserRuntime(factory, settings)
    assert await runtime.reconcile() == 1
    async with factory() as session:
        run = await session.get(ParserRun, 1)
        assert run is not None and run.status == "interrupted"
        await RunRepository(session).prepare_resume(run.id)
        await session.commit()
    runtime.start(1)
    assert await _wait_for_terminal(factory, 1) == "completed"
    await runtime.close()
    async with factory() as session:
        count = int(await session.scalar(select(func.count(Listing.id))) or 0)
        unique = int(
            await session.scalar(select(func.count(func.distinct(Listing.grailed_id)))) or 0
        )
    assert count == unique == 400
    await engine.dispose()


async def test_heartbeat_persists_progress_and_observability(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path / "stage12-heartbeat.db")
    await _seed_brands(factory, count=1)
    settings = Settings(source_mode="mock", parser_progress_interval_s=1)
    run_id = await _create_run(factory, settings)
    runtime = ParserRuntime(factory, settings)
    resources = await runtime._resources(run_id, settings)
    runtime._resources_by_run[run_id] = resources
    cancelled = asyncio.Event()
    heartbeat = asyncio.create_task(runtime._heartbeat(run_id, cancelled, settings))
    await asyncio.sleep(1.1)
    cancelled.set()
    await heartbeat
    async with factory() as session:
        run = await session.get(ParserRun, run_id)
    assert run is not None and run.stats["progress"]["tasks_total"] == 2
    assert "observability" in run.stats
    await resources.close()
    await runtime.close()
    await engine.dispose()  # type: ignore[attr-defined]
