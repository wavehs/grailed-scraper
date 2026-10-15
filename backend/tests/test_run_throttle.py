"""Repeated 429/WAF stops a run, and a run can carry its own request rate."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.parser import RunRequest
from app.core.config import Settings
from app.db.models import (
    Base,
    Brand,
    BrandSourceMap,
    ParserRunTask,
    SourceCredential,
    SourceSchema,
)
from app.db.session import create_database_engine
from app.repositories.runs import RunRepository
from app.services.parser import runtime as runtime_module
from app.services.parser.launch import LaunchError, create_run, run_settings
from app.services.parser.runtime import ParserRuntime
from app.services.sources.grailed.algolia.exceptions import AlgoliaRateLimited

NOW = datetime(2026, 10, 15, 4, tzinfo=UTC)
TASKS = 5


async def _market(path: Path, *, mapped: bool = True) -> async_sessionmaker[AsyncSession]:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{path}")
    engine = create_database_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        brand = Brand(
            id=1,
            name="Rick Owens",
            slug="rick-owens",
            aliases=[],
            include_subbrands=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add_all(
            [
                brand,
                SourceCredential(
                    source="grailed",
                    app_id="APP",
                    api_key="secret",
                    active_index="active",
                    sold_index="sold",
                    sorted_indices=[],
                    key_acl={},
                    discovered_at=NOW,
                    discovery_method="page_config",
                    verification_status="valid",
                ),
                SourceSchema(
                    source="grailed",
                    observed_fields={},
                    sample_size=1,
                    detected_at=NOW,
                    drift_score=Decimal(0),
                ),
            ]
        )
        if mapped:
            session.add(
                BrandSourceMap(
                    brand_id=1,
                    source="grailed",
                    source_designer_name="Rick Owens",
                    listings_count=10,
                    match_score=Decimal(1),
                    match_method="manual",
                    verified=True,
                    is_subbrand=False,
                    updated_at=NOW,
                )
            )
        await session.commit()
    return sessions


async def _pending_run(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as session:
        run = await RunRepository(session).create(
            mode="delta",
            budget={},
            tasks=[
                {"brand_id": 1, "index_type": "sold", "bucket_spec": {"n": index}}
                for index in range(TASKS)
            ],
        )
        await session.commit()
        return run.id


def _scripted(
    monkeypatch: pytest.MonkeyPatch,
    sessions: async_sessionmaker[AsyncSession],
    outcomes: list[str],
) -> None:
    """Each processed task follows the next outcome: 429, boom (other error) or ok."""

    script: Iterator[str] = iter(outcomes)

    async def process(self: ParserRuntime, run_id: int, task_id: int, *_: object) -> None:
        outcome = next(script)
        async with sessions() as session:
            task = await session.get(ParserRunTask, task_id)
            assert task is not None
            task.status = "running"
            if outcome == "ok":
                task.status = "done"
            await session.commit()
        if outcome == "429":
            raise AlgoliaRateLimited("search", 429)
        if outcome == "boom":
            raise ValueError("boom")

    monkeypatch.setattr(runtime_module.ParserRuntime, "_process_task", process)


async def _run_to_end(sessions: async_sessionmaker[AsyncSession], run_id: int) -> None:
    runtime = ParserRuntime(sessions, Settings())
    runtime.start(run_id)
    for _ in range(200):
        if not runtime.active_run_ids():
            break
        await asyncio.sleep(0.05)
    await runtime.close()


async def _statuses(sessions: async_sessionmaker[AsyncSession], run_id: int) -> list[str]:
    async with sessions() as session:
        return list(
            await session.scalars(
                select(ParserRunTask.status)
                .where(ParserRunTask.run_id == run_id)
                .order_by(ParserRunTask.id)
            )
        )


@pytest.mark.asyncio
async def test_three_throttled_tasks_in_a_row_stop_the_run_as_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = await _market(tmp_path / "throttle.db")
    run_id = await _pending_run(sessions)
    _scripted(monkeypatch, sessions, ["429"] * TASKS)

    await _run_to_end(sessions, run_id)

    async with sessions() as session:
        run = await RunRepository(session).get(run_id)
        assert run is not None
        assert run.status == "partial"
        assert run.stats["stop_reason"] == "throttled"
        assert any("throttled" in warning for warning in run.warnings)
        # Regroup and metrics still ran for what was collected.
        assert "grouping" in run.stats and "metrics" in run.stats
    # The untouched tasks stay pending, so a resume picks them up.
    assert await _statuses(sessions, run_id) == ["failed"] * 3 + ["pending"] * 2
    async with sessions() as session:
        resumed = await RunRepository(session).prepare_resume(run_id)
        await session.commit()
    assert "stop_reason" not in resumed.stats
    assert await _statuses(sessions, run_id) == ["pending"] * TASKS


@pytest.mark.parametrize(
    ("outcomes", "status"),
    [
        (["429", "429", "boom", "429", "429"], "failed"),
        (["429", "429", "ok", "429", "429"], "partial"),
    ],
)
@pytest.mark.asyncio
async def test_any_other_outcome_breaks_the_throttle_streak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcomes: list[str], status: str
) -> None:
    sessions = await _market(tmp_path / "streak.db")
    run_id = await _pending_run(sessions)
    _scripted(monkeypatch, sessions, outcomes)

    await _run_to_end(sessions, run_id)

    async with sessions() as session:
        run = await RunRepository(session).get(run_id)
    assert run is not None and run.status == status
    assert "stop_reason" not in run.stats
    assert "pending" not in await _statuses(sessions, run_id)


@pytest.mark.asyncio
async def test_a_run_keeps_its_own_request_rate(tmp_path: Path) -> None:
    sessions = await _market(tmp_path / "rpm.db")
    settings = Settings(requests_per_minute=900)
    async with sessions() as session:
        slow = await create_run(
            session, settings, brand_ids=[1], full=False, stats={"requests_per_minute": 30}
        )
        plain = await create_run(session, settings, brand_ids=None, full=True)

    assert slow.stats["requests_per_minute"] == 30 and "progress" in slow.stats
    assert run_settings(settings, slow).requests_per_minute == 30
    assert run_settings(settings, plain) is settings
    assert plain.mode == "full"
    with pytest.raises(ValidationError):
        RunRequest(rpm=0)


@pytest.mark.parametrize(
    ("mapped", "brand_ids", "status", "code"),
    [
        (True, [99], 404, "brand_not_found"),
        (False, [1], 409, "brand_mapping_required"),
    ],
)
@pytest.mark.asyncio
async def test_create_run_reports_why_it_cannot_plan(
    tmp_path: Path, mapped: bool, brand_ids: list[int], status: int, code: str
) -> None:
    sessions = await _market(tmp_path / "plan.db", mapped=mapped)
    async with sessions() as session:
        with pytest.raises(LaunchError) as error:
            await create_run(session, Settings(), brand_ids=brand_ids, full=False)

    assert (error.value.status, error.value.code) == (status, code)
