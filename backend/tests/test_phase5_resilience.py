"""Source-independent resilience checks for phase five."""

from __future__ import annotations

import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.runtime import SingleInstanceLock
from app.db.models import Base
from app.db.session import create_database_engine
from app.repositories.runs import RunRepository
from app.services import operations
from app.services.sources.grailed.algolia.models import AlgoliaPage, AlgoliaQuery
from app.services.sources.grailed.algolia.pagination import PaginationPlanner, PaginationSpec


class _BoundedClient:
    async def search(self, _index_name: str, query: AlgoliaQuery) -> AlgoliaPage:
        assert query.hits_per_page == 0
        return AlgoliaPage((), nb_hits=10)

    async def browse(
        self, _index_name: str, _query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage:
        assert cursor is None
        return AlgoliaPage(
            tuple({"objectID": str(value)} for value in range(10)),
            nb_hits=10,
        )

    async def multi_query(self, _requests):  # type: ignore[no-untyped-def]
        raise AssertionError("browse strategy should not use multi-query")


@pytest.mark.asyncio
async def test_bounded_pagination_stops_and_reports_truncation() -> None:
    run = PaginationPlanner(_BoundedClient()).fetch(  # type: ignore[arg-type]
        PaginationSpec(
            index_name="live",
            query=AlgoliaQuery(),
            strategy="browse",
            can_browse=True,
            max_hits=3,
        )
    )
    hits = [hit async for batch in run for hit in batch.hits]
    assert len(hits) == 3
    assert run.report.collected_hits == 3
    assert run.report.expected_hits == 10
    assert run.report.truncated is True


@pytest.mark.asyncio
async def test_sqlite_pragmas_and_windows_single_instance_lock(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'phase5.db'}",
        sqlite_busy_timeout_ms=3_210,
    )
    engine = create_database_engine(settings)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("PRAGMA journal_mode")) == "wal"
            assert await connection.scalar(text("PRAGMA synchronous")) == 1
            assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert await connection.scalar(text("PRAGMA busy_timeout")) == 3_210
    finally:
        await engine.dispose()

    lock_path = tmp_path / "app.lock"
    script = (
        "from pathlib import Path; from app.core.runtime import SingleInstanceLock; "
        f"lock=SingleInstanceLock(Path({str(lock_path)!r})); lock.acquire(); "
        "print('ready', flush=True); input(); lock.release()"
    )
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == "ready"
        second = SingleInstanceLock(lock_path)
        with pytest.raises(RuntimeError, match="another_backend_instance"):
            second.acquire()
    finally:
        process.communicate("\n", timeout=5)
    assert process.returncode == 0
    second = SingleInstanceLock(lock_path)
    second.acquire()
    second.release()


@pytest.mark.asyncio
async def test_reconcile_and_resume_preserve_checkpoints_in_each_phase(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'resume.db'}")
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        run_ids: list[int] = []
        async with sessions() as session:
            repository = RunRepository(session)
            for phase in ("fetching", "normalizing", "scoring"):
                run = await repository.create(
                    mode="full",
                    budget={},
                    tasks=[{"index_type": "active", "bucket_spec": {}}],
                )
                await repository.begin(run.id)
                await repository.set_phase(run.id, phase)
                task = (await repository.tasks(run.id))[0]
                task.status = "running" if phase == "fetching" else "done"
                task.cursor = "checkpoint" if phase == "fetching" else None
                run_ids.append(run.id)
            await session.commit()
        async with sessions() as session:
            repository = RunRepository(session)
            assert await repository.reconcile_interrupted() == 3
            await session.commit()
            for run_id in run_ids:
                resumed = await repository.get(run_id)
                assert resumed is not None and resumed.status == "interrupted"
                await repository.prepare_resume(run_id)
                assert resumed.status == "pending" and resumed.phase == "fetching"
            tasks = [task for run_id in run_ids for task in await repository.tasks(run_id)]
            assert [task.status for task in tasks] == ["pending", "done", "done"]
            assert tasks[0].cursor == "checkpoint"
            tasks[0].status = "truncated"
            assert await repository.aggregate_status(run_ids[0]) == "partial"
    finally:
        await engine.dispose()


def test_verified_backup_restore_apply_and_integrity(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(operations, "PROJECT_ROOT", tmp_path)
    database = tmp_path / "grailed.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('before')")
        connection.commit()
    settings = Settings(database_url=f"sqlite+aiosqlite:///{database}")
    backup = operations.backup_database(
        settings, destination=tmp_path / "data" / "backups" / "manual.sqlite3"
    )
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE marker SET value='after'")
        connection.commit()
    result = operations.restore_database(settings, backup, apply=True)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("before",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert result["apply"] is True
    assert result["valid"] is True
    with sqlite3.connect(str(result["safety_backup"])) as safety:
        assert safety.execute("PRAGMA integrity_check").fetchone() == ("ok",)
