"""Coverage summaries persist through the existing parser run tables."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, ParserRun, ParserRunTask
from app.repositories.fetching import FetchReportRepository
from app.services.sources.base.models import CoverageReport


@pytest.mark.asyncio
async def test_task_coverage_updates_run_summary_without_schema_changes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'coverage.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 8, tzinfo=UTC)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with sessions() as session:
        parser_run = ParserRun(
            source="grailed",
            mode="full",
            status="running",
            dry_run=False,
            degraded_mode=False,
            requests_made=0,
            warnings=[],
            stats={},
            created_at=now,
        )
        session.add(parser_run)
        await session.flush()
        task = ParserRunTask(
            run_id=parser_run.id,
            index_type="sold",
            status="running",
            attempts=1,
            hits_collected=0,
        )
        session.add(task)
        await session.flush()

        report = CoverageReport.calculate(
            expected_hits=100,
            collected_hits=75,
            truncated=True,
            warnings=("dense bucket",),
        )
        await FetchReportRepository(session).finish_task(
            task.id, report, fetch_tier="T1", cursor="75", now=now
        )
        await session.commit()

        assert task.status == "truncated"
        assert task.coverage == Decimal("0.75")
        assert parser_run.coverage_avg == Decimal("0.75")
        assert parser_run.degraded_mode is False
        assert parser_run.tier_used == "T1"
        assert parser_run.stats["fetching"]["partial"] == 1
        assert "dense bucket" in parser_run.warnings[0]

    await engine.dispose()


@pytest.mark.asyncio
async def test_an_estimated_expected_count_is_stored_and_counted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'estimate.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 15, tzinfo=UTC)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with sessions() as session:
        parser_run = ParserRun(
            source="grailed",
            mode="full",
            status="running",
            dry_run=False,
            degraded_mode=False,
            requests_made=0,
            warnings=[],
            stats={},
            created_at=now,
        )
        session.add(parser_run)
        await session.flush()
        tasks = [
            ParserRunTask(run_id=parser_run.id, index_type=index, status="running", attempts=1)
            for index in ("sold", "live")
        ]
        session.add_all(tasks)
        await session.flush()

        repository = FetchReportRepository(session)
        estimated = CoverageReport.calculate(
            expected_hits=3_000, collected_hits=2_900, expected_source="estimated"
        )
        exact = CoverageReport.calculate(expected_hits=10, collected_hits=10)
        await repository.finish_task(tasks[0].id, estimated, fetch_tier="T1", cursor=None, now=now)
        await repository.finish_task(tasks[1].id, exact, fetch_tier="T1", cursor=None, now=now)

        assert [task.expected_source for task in tasks] == ["estimated", "exact"]
        assert parser_run.stats["fetching"]["estimated"] == 1

    await engine.dispose()
