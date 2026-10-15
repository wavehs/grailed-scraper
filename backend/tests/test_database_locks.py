"""Causes of ``database is locked``: short transactions, retried API writes, shared pragmas."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.api import (
    brands,
    discovery,
    fees,
    groups,
    listings,
    mutation,
    parser,
    routes,
    schedule,
    settings,
    trend_export,
    trends,
    watchlist,
)
from app.api.errors import install_exception_handlers
from app.api.mutation import MutationRoute
from app.core.config import Settings
from app.db.models import Base, Brand, ModelGroup
from app.db.session import checkpoint_database, create_database_engine, get_db
from app.main import app
from app.services import per_brand
from app.services.per_brand import recompute_brands, regroup_brands

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _locked() -> OperationalError:
    return OperationalError("UPDATE x", {}, sqlite3.OperationalError("database is locked"))


def _flaky_app(failures: int, error: OperationalError) -> tuple[FastAPI, list[str]]:
    events: list[str] = []

    async def resource() -> AsyncIterator[int]:
        attempt = sum(event.startswith("open") for event in events) + 1
        events.append(f"open{attempt}")
        try:
            yield attempt
        finally:
            events.append(f"close{attempt}")

    router = APIRouter(route_class=MutationRoute)

    # A local dependency cannot sit in a string annotation, hence the plain defaults.
    @router.post("/write")
    async def write(attempt: int = Depends(resource)) -> dict[str, int]:  # noqa: B008
        if attempt <= failures:
            raise error
        return {"attempt": attempt}

    @router.get("/read")
    async def read(attempt: int = Depends(resource)) -> dict[str, int]:  # noqa: B008
        raise error

    flaky = FastAPI()
    install_exception_handlers(flaky)
    flaky.include_router(router)
    return flaky, events


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mutation, "MUTATION_BACKOFF_S", 0.0)


def test_a_locked_write_is_retried_with_a_fresh_session_each_time() -> None:
    app, events = _flaky_app(failures=2, error=_locked())

    response = TestClient(app).post("/write")

    assert response.status_code == 200 and response.json() == {"attempt": 3}
    # Each failed attempt releases its session before the next one opens.
    assert events == ["open1", "close1", "open2", "close2", "open3", "close3"]


def test_a_write_still_locked_after_three_attempts_is_a_503() -> None:
    app, events = _flaky_app(failures=10, error=_locked())

    response = TestClient(app).post("/write")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_locked"
    assert events.count("close3") == 1 and "open4" not in events


def test_reads_and_other_database_errors_are_not_retried() -> None:
    other = OperationalError("x", {}, sqlite3.OperationalError("no such table: x"))
    app, events = _flaky_app(failures=10, error=other)
    client = TestClient(app, raise_server_exceptions=False)

    assert client.post("/write").status_code == 500
    assert client.get("/read").status_code == 500
    assert events == ["open1", "close1", "open2", "close2"]


@pytest.mark.parametrize(
    "module",
    [
        brands,
        discovery,
        fees,
        groups,
        listings,
        parser,
        routes,
        schedule,
        settings,
        trend_export,
        trends,
        watchlist,
    ],
)
def test_every_api_router_retries_locked_writes(module: ModuleType) -> None:
    declared = [route for route in module.router.routes if isinstance(route, APIRoute)]
    assert declared
    assert all(isinstance(route, MutationRoute) for route in declared)


def test_the_real_app_retries_a_locked_settings_write() -> None:
    calls: list[int] = []

    async def locked_db() -> AsyncIterator[AsyncSession]:
        calls.append(1)
        raise _locked()
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = locked_db
    try:
        response = TestClient(app).patch("/api/settings", json={"requests_per_minute": 90})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503, response.text
    assert len(calls) == 3


async def _brands(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_database_engine(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'locks.db'}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for index, name in enumerate(("Rick Owens", "Balenciaga"), start=1):
            session.add(
                Brand(
                    id=index,
                    name=name,
                    slug=name.lower().replace(" ", "-"),
                    aliases=[],
                    include_subbrands=False,
                    created_at=T0,
                    updated_at=T0,
                )
            )
        await session.commit()
    return factory


def _count_commits(session: AsyncSession) -> list[int]:
    commits: list[int] = []
    original = session.commit

    async def counting() -> None:
        commits.append(1)
        await original()

    session.commit = counting  # type: ignore[method-assign]
    return commits


@pytest.mark.asyncio
async def test_regroup_and_metrics_commit_once_per_brand(tmp_path: Path) -> None:
    factory = await _brands(tmp_path)
    async with factory() as session:
        commits = _count_commits(session)

        grouped = await regroup_brands(session, None, full=True)
        assert [item.brand_id for item in grouped.brands] == [1, 2]
        assert len(commits) == 2

        computed = await recompute_brands(session, [2, 1, 2])
        assert computed.brands == 2
        # Plus one for the cross-brand ranking and one for the first seasonal profiles.
        assert len(commits) == 6


@pytest.mark.asyncio
async def test_checkpoint_truncates_the_wal(tmp_path: Path) -> None:
    factory = await _brands(tmp_path)
    wal = tmp_path / "locks.db-wal"
    async with factory() as session:
        await session.execute(text("UPDATE brands SET aliases = '[]'"))
        await session.commit()
        assert wal.stat().st_size > 0

        result = await checkpoint_database(session)

    assert result["busy"] == 0
    assert wal.stat().st_size == 0


def test_cli_never_builds_an_engine_without_the_shared_pragmas() -> None:
    source = inspect.getsource(cli)

    assert "get_database_url" not in source
    assert source.count("create_database_engine(settings)") >= 6


@pytest.mark.asyncio
async def test_retry_locked_rolls_back_and_reraises_after_the_last_attempt(
    tmp_path: Path,
) -> None:
    factory = await _brands(tmp_path)
    calls: list[int] = []

    async def locked() -> int:
        calls.append(1)
        raise _locked()

    async def flaky() -> int:
        calls.append(1)
        if len(calls) < 2:
            raise _locked()
        return len(calls)

    async with factory() as session:
        with pytest.raises(OperationalError):
            await mutation.retry_locked(session, locked, where="test")
        assert len(calls) == 3
        calls.clear()
        assert await mutation.retry_locked(session, flaky, where="test") == 2


def test_a_rule_is_saved_before_a_regroup_that_keeps_losing_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = asyncio.run(_brands(tmp_path))
    group_id = asyncio.run(_model_group(factory))
    regroups: list[int] = []

    async def locked_regroup(*_: object, **__: object) -> None:
        regroups.append(1)
        raise _locked()

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    monkeypatch.setattr(groups, "regroup_brands", locked_regroup)
    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).patch(f"/api/groups/{group_id}", json={"name": "Le Cagole"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "regroup_pending"
    # Only the regroup is retried: re-running the handler would repeat the saved edit.
    assert len(regroups) == 3
    saved = asyncio.run(_group_name(factory, group_id))
    assert saved == "Le Cagole"


async def _model_group(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        group = ModelGroup(
            brand_id=2,
            product_type="bag",
            slug="cagole",
            name="Cagole",
            aliases=[],
            status="confirmed",
            source="user",
            created_at=T0,
            updated_at=T0,
        )
        session.add(group)
        await session.commit()
        return group.id


async def _group_name(factory: async_sessionmaker[AsyncSession], group_id: int) -> str | None:
    async with factory() as session:
        group = await session.get(ModelGroup, group_id)
        return group.name if group else None


def _client_for(factory: async_sessionmaker[AsyncSession], commits: list[bool]) -> TestClient:
    """A client whose sessions record, per commit, whether the grouping lock was held."""

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            original = session.commit

            async def recording() -> None:
                commits.append(groups._fallback_lock.locked())
                await original()

            session.commit = recording  # type: ignore[method-assign]
            yield session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_a_rule_commits_before_waiting_for_the_grouping_lock(tmp_path: Path) -> None:
    factory = asyncio.run(_brands(tmp_path))
    group_id = asyncio.run(_model_group(factory))
    commits: list[bool] = []
    try:
        response = _client_for(factory, commits).patch(
            f"/api/groups/{group_id}", json={"name": "Le Cagole"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    # The rule's write lock is released before the request queues behind a parser regroup.
    assert commits[0] is False


def test_marking_not_a_model_survives_a_retried_regroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = asyncio.run(_brands(tmp_path))
    group_id = asyncio.run(_model_group(factory))
    attempts: list[int] = []
    real_regroup = per_brand.regroup_brands

    async def flaky_regroup(*args: object, **kwargs: object) -> object:
        attempts.append(1)
        if len(attempts) == 1:
            raise _locked()
        return await real_regroup(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(groups, "regroup_brands", flaky_regroup)
    try:
        response = _client_for(factory, []).post(f"/api/groups/{group_id}/not-model")
    finally:
        app.dependency_overrides.clear()

    # The rollback before the retry expires the group; the response must not read it stale.
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ignored" and len(attempts) == 2


def test_a_regroup_is_not_repeated_when_only_its_metrics_lose_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = asyncio.run(_brands(tmp_path))
    regroups: list[int] = []
    metrics: list[int] = []
    real_regroup = per_brand.regroup_brands

    async def counting_regroup(*args: object, **kwargs: object) -> object:
        regroups.append(1)
        return await real_regroup(*args, **kwargs)  # type: ignore[arg-type]

    async def locked_metrics(*_: object, **__: object) -> None:
        metrics.append(1)
        raise _locked()

    monkeypatch.setattr(groups, "regroup_brands", counting_regroup)
    monkeypatch.setattr(groups, "recompute_brands", locked_metrics)
    try:
        response = _client_for(factory, []).post(
            "/api/grouping/regroup", json={"brand_ids": [2], "full": True}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "database_locked"
    assert len(regroups) == 1 and len(metrics) == 3
