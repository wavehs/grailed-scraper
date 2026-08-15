from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler

import pytest
import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import ApiError
from app.api.settings import SettingsPatch, update_settings
from app.core.config import Settings
from app.core.logging import configure_logging, mask_sensitive_data
from app.core.privacy import compliance_reasons, require_live_compliance, seller_identity
from app.db.models import Base
from app.services.operations import backup_database, restore_database, retention
from app.services.parser.observability import RunMetrics
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.models import AlgoliaQuery
from app.services.transport.protocols import HttpResponse


@dataclass(frozen=True)
class _Seed:
    app_id: str = "fixture-app"
    api_key: str = "fixture-key"
    algolia_agent: str | None = "fixture-agent"
    session_headers: tuple[tuple[str, str], ...] = ()


class _Transport:
    async def request(self, method: str, url: str, **_: object) -> HttpResponse:
        return HttpResponse(200, {}, b'{"hits":[],"nbHits":0}', url)

    async def close(self) -> None:
        return None


def test_central_redaction_handles_nested_headers_urls_and_bearer_tokens() -> None:
    masked = mask_sensitive_data(
        {
            "headers": {"Authorization": "Bearer extremely-secret"},
            "url": "https://user:pass@example.test/path?x-algolia-api-key=abcdef123456",
            "nested": [{"unknown_token": "abcdef123456"}],
        }
    )
    serialized = json.dumps(masked)
    assert "extremely-secret" not in serialized
    assert "user:pass" not in serialized
    assert "abcdef123456" not in serialized


def test_logging_writes_json_to_rotating_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    configure_logging(Settings(log_directory=tmp_path, log_level="INFO"))
    structlog.get_logger("stage11").info("security_event", api_key="abcdef123456")
    handlers = list(logging.getLogger().handlers)
    for handler in handlers:
        handler.flush()
    record = json.loads((tmp_path / "parser.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "security_event"
    assert record["api_key"] == "abcd****"
    required = {
        "ts",
        "level",
        "request_id",
        "run_id",
        "task_id",
        "source",
        "tier",
        "duration_ms",
        "msg",
    }
    assert required <= record.keys()
    rotating = [handler for handler in handlers if isinstance(handler, RotatingFileHandler)]
    assert len(rotating) == 2
    assert all(handler.maxBytes == 10 * 1024 * 1024 for handler in rotating)
    assert all(handler.backupCount == 5 for handler in rotating)
    for handler in handlers:
        handler.close()
    logging.getLogger().handlers.clear()


def test_seller_identity_modes_are_explicit_and_stable() -> None:
    none = Settings(store_seller_identity="none")
    hashed = Settings(store_seller_identity="hashed", seller_identity_salt="local-test-salt")
    plain = Settings(store_seller_identity="plain")
    assert seller_identity(" Alice ", none) is None
    assert seller_identity(" Alice ", hashed) == seller_identity("alice", hashed)
    assert len(seller_identity("Alice", hashed) or "") == 64
    assert seller_identity(" Alice ", plain) == "alice"


def test_default_hash_salt_is_generated_once_outside_the_database(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(store_seller_identity="hashed")
    first = seller_identity("Alice", settings, root=tmp_path)
    second = seller_identity(" alice ", settings, root=tmp_path)
    salt_file = tmp_path / "data" / "secrets" / "seller_identity_salt"
    assert first == second
    assert salt_file.is_file() and len(salt_file.read_text(encoding="ascii")) == 64
    assert compliance_reasons(
        Settings(
            source_mode="live",
            store_seller_identity="plain",
            live_compliance_acknowledged=False,
        )
    ) == ["live_compliance_not_acknowledged", "seller_identity_plaintext_enabled"]


def test_compliance_limits_and_live_ack_are_enforced() -> None:
    with pytest.raises(ValidationError):
        Settings(requests_per_minute=91)
    with pytest.raises(ValidationError):
        Settings(max_concurrent_requests=4)
    with pytest.raises(RuntimeError, match="live_compliance_not_acknowledged"):
        require_live_compliance(
            Settings(source_mode="live", live_compliance_acknowledged=False)
        )
    require_live_compliance(Settings(source_mode="live", live_compliance_acknowledged=True))


def test_run_metrics_resume_keeps_duration_and_latency() -> None:
    metrics = RunMetrics()
    metrics.record_response("T1", 200, 10)
    snapshot = metrics.snapshot(duration_s=5)
    resumed = RunMetrics.resume(snapshot)
    resumed.record_response("T1", 200, 30)
    result = resumed.snapshot()
    assert result["requests_total"] == 2
    assert result["avg_latency_ms"] == 20
    assert result["duration_s"] >= 5

    reconciled = RunMetrics.resume(snapshot, minimum_requests=7, tier="T1")
    assert reconciled.snapshot()["requests_total"] == 7


@pytest.mark.asyncio
async def test_plain_setting_requires_nonpersistent_confirmation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(ApiError) as caught:
                await update_settings(
                    SettingsPatch(store_seller_identity="plain"), session, Settings()
                )
            assert caught.value.code == "plain_seller_identity_confirmation_required"
        async with factory() as session:
            response = await update_settings(
                SettingsPatch(
                    store_seller_identity="plain",
                    confirm_plain_seller_identity=True,
                ),
                session,
                Settings(),
            )
            assert response.groups["privacy"]["store_seller_identity"].value == "plain"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_algolia_metrics_include_cache_hits_and_request_latency() -> None:
    metrics = RunMetrics()
    client = AlgoliaClient(_Transport(), _Seed(), hosts=("https://algolia.test",), metrics=metrics)
    query = AlgoliaQuery(query="Rick Owens", hits_per_page=1)
    try:
        await client.search("products_active", query)
        await client.search("products_active", query)
    finally:
        await client._transport.close()
    snapshot = metrics.snapshot()
    assert snapshot["requests_total"] == 1
    assert snapshot["cache_hits"] == 1
    assert snapshot["cache_hit_rate"] == 0.5
    assert snapshot["p95_latency_ms"] >= 0


def test_retention_backup_and_restore_preview_are_safe(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "grailed.db"
    backups = tmp_path / "backups"
    backups.mkdir()
    expired_backup = backups / "expired.sqlite3"
    expired_backup.write_bytes(b"old")
    expired_timestamp = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    os.utime(expired_backup, (expired_timestamp, expired_timestamp))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE listings (last_seen_at TEXT, raw_json TEXT, raw_json_purged_at TEXT)"
        )
        connection.execute(
            "INSERT INTO listings VALUES (?, ?, NULL)",
            ((datetime.now(UTC) - timedelta(days=100)).isoformat(), '{"title":"old"}'),
        )
        connection.commit()
    settings = Settings(database_url=f"sqlite+aiosqlite:///{database.as_posix()}")
    preview = retention(settings, backup_dir=backups)
    assert preview.raw_rows == 1 and preview.backup_files == 1 and not preview.apply
    applied = retention(settings, apply=True, backup_dir=backups)
    assert applied.raw_rows == 1 and applied.backup_files == 1
    assert not expired_backup.exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT raw_json FROM listings").fetchone()[0] == "{}"
    with pytest.raises(ValueError, match="inside data/backups"):
        backup_database(settings, destination=tmp_path / "outside.sqlite3", backup_dir=backups)
    backup = backup_database(settings, backup_dir=backups)
    assert backup.is_file()
    restore_preview = restore_database(settings, backup)
    assert restore_preview == {
        "apply": False,
        "source": str(backup),
        "target": str(database.resolve()),
        "valid": True,
    }
