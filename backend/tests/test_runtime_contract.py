"""Production runtime identity and startup safety contracts."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.runtime import inspect_runtime, require_startup_ready, validate_production


def test_production_rejects_unknown_revision_lan_bind_and_broad_cors() -> None:
    settings = Settings(
        environment="production",
        backend_bind_host="0.0.0.0",
        frontend_bind_host="0.0.0.0",
        cors_origins=["*"],
    )

    assert validate_production(settings, "unknown") == [
        "revision_unknown",
        "backend_bind_not_loopback",
        "frontend_bind_not_loopback",
        "frontend_origin_not_local",
    ]


def test_production_rejects_database_without_alembic_revision(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "empty.db"
    database.touch()
    settings = Settings(
        environment="production",
        revision="release-1",
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        data_directory=tmp_path / "data",
        log_directory=tmp_path / "logs",
        proxy_url="http://user:runtime-secret@proxy.test:8080",
        seller_identity_salt="runtime-secret",
    )
    engine = create_async_engine(settings.database_url)
    try:
        report = asyncio.run(inspect_runtime(settings, engine))
    finally:
        asyncio.run(engine.dispose())

    assert report["schema"]["status"] == "unknown"
    assert "release-1" in str(report)
    assert "runtime-secret" not in str(report)
    with pytest.raises(RuntimeError, match="database_schema_unknown") as error:
        require_startup_ready(settings, report)
    assert "runtime-secret" not in str(error.value)
