"""Backup rotation, self-contained backups and a guarded manual VACUUM."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections import namedtuple
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.runtime import SingleInstanceLock
from app.services import operations
from app.services.operations import backup_database, restore_database, retention, vacuum_database


def _settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    return Settings(
        data_directory=data,
        database_url=f"sqlite+aiosqlite:///{(data / 'grailed.db').as_posix()}",
    )


def _database(settings: Settings, rows: int = 200) -> Path:
    database = settings.data_directory / "grailed.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE listings (last_seen_at TEXT, raw_json TEXT, raw_json_purged_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO listings VALUES (?, ?, NULL)",
            [(datetime.now(UTC).isoformat(), "x" * 2_000) for _ in range(rows)],
        )
        connection.commit()
    return database


def _aged(path: Path, days: float) -> Path:
    path.write_bytes(b"backup")
    stamp = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_backups_keep_the_newest_three_and_lose_their_sidecars(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _database(settings, rows=1)
    backups = settings.data_directory / "backups"
    backups.mkdir()
    files = [_aged(backups / f"grailed-{day}.sqlite3", day) for day in range(1, 6)]
    oldest = files[-1]
    _aged(Path(f"{oldest}-wal"), 5)
    _aged(Path(f"{oldest}-shm"), 5)
    _aged(backups / "gone.sqlite3-wal", 1)
    manual = _aged(backups / "grailed-empty-mac.db", 90)

    preview = retention(settings)
    applied = retention(settings, apply=True)

    assert (preview.backup_files, preview.sidecar_files) == (2, 3)
    assert (applied.backup_files, applied.sidecar_files) == (2, 3)
    assert sorted(path.name for path in backups.iterdir()) == sorted(
        [*(path.name for path in files[:3]), manual.name]
    )


def test_an_old_backup_expires_even_within_the_newest_three(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _database(settings, rows=1)
    backups = settings.data_directory / "backups"
    backups.mkdir()
    fresh = _aged(backups / "grailed-new.sqlite3", 1)
    _aged(backups / "grailed-old.sqlite3", 45)

    result = retention(settings, apply=True)

    assert result.backup_files == 1
    assert list(backups.glob("*.sqlite3")) == [fresh]


def test_a_backup_is_one_self_contained_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _database(settings)

    backup = backup_database(settings)

    assert backup.parent == (settings.data_directory / "backups").resolve()
    assert sorted(path.name for path in backup.parent.iterdir()) == [backup.name]
    with closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("SELECT count(*) FROM listings").fetchone()[0] == 200


def test_restore_looks_for_the_running_app_in_the_data_directory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _database(settings)
    backup = backup_database(settings)
    (settings.data_directory / "app.pid").write_text(str(os.getpid()), encoding="ascii")

    with pytest.raises(RuntimeError, match="Stop the application"):
        restore_database(settings, backup, apply=True)


def test_vacuum_shrinks_the_database_and_reports_it(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = _database(settings)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DELETE FROM listings")
        connection.commit()

    result = vacuum_database(settings)

    before, after = result["before_bytes"], result["after_bytes"]
    assert result["status"] == "ok"
    assert isinstance(before, int) and isinstance(after, int) and after < before
    assert isinstance(result["duration_s"], float)


def test_vacuum_refuses_while_the_app_runs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _database(settings)
    lock = SingleInstanceLock(settings.data_directory / "app.lock")
    lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="Stop the application"):
            vacuum_database(settings)
    finally:
        lock.release()


def test_vacuum_refuses_without_twice_the_database_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    database = _database(settings)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage(0, 0, database.stat().st_size))

    with pytest.raises(RuntimeError, match="free space"):
        vacuum_database(settings)


def test_retention_purges_old_raw_data_in_batches(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = _database(settings, rows=7)
    old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE listings SET last_seen_at = ? WHERE rowid <= 5", (old,))
        connection.commit()
    statements: list[str] = []

    def trace(connection: sqlite3.Connection) -> None:
        connection.set_trace_callback(statements.append)

    result = retention(settings, apply=True, batch_size=2, on_connect=trace)

    assert result.raw_rows == 5
    assert sum(statement.startswith("UPDATE listings") for statement in statements) == 3
    with closing(sqlite3.connect(database)) as connection:
        purged = connection.execute("SELECT count(*) FROM listings WHERE raw_json = '{}'")
        assert purged.fetchone()[0] == 5


def test_a_backup_that_fails_its_check_leaves_no_backup_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _database(settings, rows=1)

    def corrupt(database: Path) -> None:
        raise ValueError("SQLite integrity_check failed")

    monkeypatch.setattr(operations, "_require_integrity", corrupt)

    with pytest.raises(ValueError, match="integrity"):
        backup_database(settings)

    assert list((settings.data_directory / "backups").iterdir()) == []
