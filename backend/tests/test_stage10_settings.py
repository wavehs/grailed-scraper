"""Stage 10 safe settings API and runtime snapshot contracts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.services.parser.runtime import ParserRuntime


async def _database(
    tmp_path: Path,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage10.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


def test_settings_api_persists_validated_overrides_and_origins(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = asyncio.run(_database(tmp_path))

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(
        source_mode="mock", requests_per_minute=12
    )
    try:
        with TestClient(app) as client:
            before = client.get("/api/settings")
            updated = client.patch(
                "/api/settings",
                json={"requests_per_minute": 24, "proxy_rotation_mode": "round_robin"},
            )
            after = client.get("/api/settings")
            invalid = client.patch("/api/settings", json={"requests_per_minute": 91})
            unknown = client.patch("/api/settings", json={"algolia_api_key": "secret"})
    finally:
        app.dependency_overrides.clear()

    assert before.status_code == 200
    assert before.json()["groups"]["parser"]["requests_per_minute"] == {
        "value": 12,
        "origin": "env",
    }
    assert updated.status_code == 200
    assert after.json()["groups"]["parser"]["requests_per_minute"] == {
        "value": 24,
        "origin": "database",
    }
    assert invalid.status_code == unknown.status_code == 422
    assert "secret" not in after.text.casefold()
    asyncio.run(engine.dispose())


async def test_runtime_captures_settings_snapshot_for_each_started_run(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    engine, factory = await _database(tmp_path)
    runtime = ParserRuntime(factory, Settings(source_mode="mock", requests_per_minute=10))
    captured: list[int] = []

    async def execute(run_id: int, _: asyncio.Event, settings: Settings) -> None:
        del run_id
        captured.append(settings.requests_per_minute)

    monkeypatch.setattr(runtime, "_execute", execute)
    runtime.start(1, settings=Settings(source_mode="mock", requests_per_minute=20))
    for _ in range(20):
        if captured:
            break
        await asyncio.sleep(0)
    await runtime.close()
    assert captured == [20]
    await engine.dispose()
