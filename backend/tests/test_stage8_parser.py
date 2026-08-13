"""Offline contracts for the stage-eight parser runtime and API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.parser import get_parser_runtime
from app.cli import run_canary
from app.core.config import Settings, get_settings
from app.db.models import (
    Base,
    Brand,
    BrandSourceMap,
    Listing,
    ParserRun,
    ParserRunTask,
    ScoringSnapshot,
)
from app.db.session import get_db
from app.main import app
from app.repositories.runs import RunRepository
from app.services.parser.planner import ParserPlanner
from app.services.parser.runtime import ParserRuntime


async def _database(tmp_path) -> tuple[object, async_sessionmaker[AsyncSession]]:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage8.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def _seed_brand(factory: async_sessionmaker[AsyncSession]) -> int:
    now = datetime.now(UTC)
    async with factory() as session:
        brand = Brand(
            name="Rick Owens",
            slug="rick-owens",
            aliases=["Rick Owens"],
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
                source_designer_name="Rick Owens",
                source_slug="rick-owens",
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
        return brand.id


async def test_planner_builds_brand_index_tasks_and_budget(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = await _database(tmp_path)
    brand_id = await _seed_brand(factory)
    async with factory() as session:
        plan = await ParserPlanner(session, Settings(source_mode="mock")).build(
            mode="delta", brand_ids=[brand_id]
        )
    assert [item.index_type for item in plan.tasks] == ["active", "sold"]
    assert all(item.status == "pending" for item in plan.tasks)
    assert plan.budget["estimated_requests"] >= 2
    await engine.dispose()  # type: ignore[attr-defined]


async def test_run_repository_reconciles_and_resumes_checkpoints(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = await _database(tmp_path)
    async with factory() as session:
        repository = RunRepository(session)
        run = await repository.create(
            mode="full",
            budget={"estimated_requests": 1},
            tasks=[
                {
                    "brand_id": None,
                    "index_type": "active",
                    "bucket_spec": {},
                    "status": "pending",
                }
            ],
        )
        await repository.begin(run.id)
        task = (await repository.tasks(run.id))[0]
        task.status = "running"
        task.cursor = "checkpoint-1"
        await session.commit()
        assert await repository.reconcile_interrupted() == 1
        await session.commit()
        assert run.status == "interrupted"
        assert task.status == "pending"
        await repository.prepare_resume(run.id)
        assert run.status == "pending"
        assert task.cursor == "checkpoint-1"
    await engine.dispose()  # type: ignore[attr-defined]


async def test_runtime_completes_mock_run_without_duplicates(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = await _database(tmp_path)
    brand_id = await _seed_brand(factory)
    settings = Settings(source_mode="mock", parser_max_concurrency=2)
    async with factory() as session:
        plan = await ParserPlanner(session, settings).build(mode="full", brand_ids=[brand_id])
        run = await RunRepository(session).create(
            mode="full",
            budget=plan.budget,
            tasks=[item.persisted() for item in plan.tasks],
        )
        run_id = run.id
        await session.commit()
    runtime = ParserRuntime(factory, settings)
    runtime.start(run_id)
    for _ in range(400):
        await asyncio.sleep(0.025)
        async with factory() as session:
            status = await session.scalar(
                select(ParserRun.status).where(ParserRun.id == run_id)
            )
        if status in {"completed", "partial", "failed"}:
            break
    await runtime.close()
    async with factory() as session:
        loaded_run = await session.get(ParserRun, run_id)
        count = int(await session.scalar(select(func.count(Listing.id))) or 0)
        unique = int(
            await session.scalar(select(func.count(func.distinct(Listing.grailed_id)))) or 0
        )
        tasks = list(
            await session.scalars(
                select(ParserRunTask).where(ParserRunTask.run_id == run_id)
            )
        )
    assert loaded_run is not None and loaded_run.status == "completed"
    assert all(task.status == "done" for task in tasks)
    assert count == unique == 400
    assert loaded_run.stats["scoring"]["status"] == "completed"
    assert loaded_run.stats["scoring"]["model_version"] == "opportunity-v1"
    async with factory() as session:
        snapshots = int(
            await session.scalar(select(func.count(ScoringSnapshot.id))) or 0
        )
    assert snapshots == 8
    await engine.dispose()  # type: ignore[attr-defined]


def test_parser_api_dry_run_writes_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    async def scenario() -> tuple[object, async_sessionmaker[AsyncSession], int]:
        engine, factory = await _database(tmp_path)
        brand_id = await _seed_brand(factory)
        return engine, factory, brand_id

    engine, factory, brand_id = asyncio.run(scenario())

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    class RuntimeStub:
        def start(self, _: int) -> None:
            raise AssertionError("dry-run must not start a job")

        def active_run_ids(self) -> list[int]:
            return []

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(source_mode="mock")
    app.dependency_overrides[get_parser_runtime] = RuntimeStub
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/parser/run",
                json={"mode": "full", "brand_ids": [brand_id], "dry_run": True},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["dry_run"] is True

    async def counts() -> tuple[int, int, int]:
        async with factory() as session:
            return (
                int(await session.scalar(select(func.count(ParserRun.id))) or 0),
                int(await session.scalar(select(func.count(ParserRunTask.id))) or 0),
                int(await session.scalar(select(func.count(Listing.id))) or 0),
            )

    assert asyncio.run(counts()) == (0, 0, 0)
    asyncio.run(engine.dispose())  # type: ignore[attr-defined]


def test_parser_api_run_lifecycle_and_health(tmp_path) -> None:  # type: ignore[no-untyped-def]
    async def scenario() -> tuple[object, async_sessionmaker[AsyncSession], int]:
        engine, factory = await _database(tmp_path)
        brand_id = await _seed_brand(factory)
        return engine, factory, brand_id

    engine, factory, brand_id = asyncio.run(scenario())

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    class RuntimeStub:
        def __init__(self) -> None:
            self.started: list[int] = []

        def start(self, run_id: int) -> None:
            self.started.append(run_id)

        def cancel(self, _: int) -> bool:
            return False

        def active_run_ids(self) -> list[int]:
            return []

    runtime = RuntimeStub()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(source_mode="mock")
    app.dependency_overrides[get_parser_runtime] = lambda: runtime
    try:
        with TestClient(app) as client:
            started = client.post(
                "/api/parser/run", json={"mode": "full", "brand_ids": [brand_id]}
            )
            run_id = started.json()["run"]["id"]
            listed = client.get("/api/parser/runs")
            detail = client.get(f"/api/parser/runs/{run_id}")
            progress = client.get(f"/api/parser/runs/{run_id}/progress")
            report = client.get(f"/api/parser/runs/{run_id}/report")
            cancelled = client.post(f"/api/parser/runs/{run_id}/cancel")
            resumed = client.post(f"/api/parser/runs/{run_id}/resume")
            health = client.get("/api/parser/health")
    finally:
        app.dependency_overrides.clear()
    assert started.status_code == 202
    assert listed.json()["total"] == 1
    assert len(detail.json()["tasks"]) == 2
    assert progress.json()["phase"] == "planning"
    assert report.json()["run"]["id"] == run_id
    assert cancelled.json()["status"] == "cancelled"
    assert resumed.json()["status"] == "pending"
    assert runtime.started == [run_id, run_id]
    assert health.status_code == 200
    assert health.json()["status"] == "ready"
    assert "api_key" not in health.text.casefold()
    asyncio.run(engine.dispose())  # type: ignore[attr-defined]


async def test_mock_canary_is_bounded_and_non_persistent() -> None:
    result = await run_canary(Settings(source_mode="mock"), "Rick Owens", 7)
    assert result["status"] == "ok"
    assert result["fetched"] == result["valid"] == 7


async def test_stage8_model_exposes_progress_columns(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, _ = await _database(tmp_path)
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        columns = await connection.run_sync(
            lambda sync: {column["name"] for column in inspect(sync).get_columns("parser_runs")}
        )
    assert {"phase", "heartbeat_at"} <= columns
    await engine.dispose()  # type: ignore[attr-defined]
