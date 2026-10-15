"""Safe local SQLite backup, restore, raw-data retention and manual VACUUM."""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from app.core.config import Settings
from app.core.runtime import SingleInstanceLock
from app.db.session import get_database_url

BACKUP_GLOB = "*.sqlite3"
SIDECAR_SUFFIXES = ("-wal", "-shm")
# VACUUM rewrites the database into a copy and the WAL can grow to its size as well.
VACUUM_FREE_FACTOR = 2


@dataclass(frozen=True, slots=True)
class RetentionResult:
    apply: bool
    raw_rows: int
    backup_files: int
    sidecar_files: int
    raw_cutoff: str
    backup_cutoff: str


def sqlite_path(settings: Settings) -> Path:
    prefix = "sqlite+aiosqlite:///"
    url = get_database_url(settings)
    if not url.startswith(prefix) or url.endswith(":memory:"):
        raise ValueError("This operation requires a file-backed SQLite database")
    return Path(url.removeprefix(prefix)).resolve()


def backup_directory(settings: Settings) -> Path:
    return (settings.data_directory / "backups").resolve()


def retention(
    settings: Settings,
    *,
    apply: bool = False,
    now: datetime | None = None,
    backup_dir: Path | None = None,
) -> RetentionResult:
    current = now or datetime.now(UTC)
    raw_cutoff = current - timedelta(days=settings.raw_data_retention_days)
    backup_cutoff = current - timedelta(days=settings.backup_retention_days)
    database = sqlite_path(settings)
    backups = (backup_dir or backup_directory(settings)).resolve()
    raw_rows = 0
    if database.exists():
        with sqlite3.connect(database) as connection:
            raw_rows = int(
                connection.execute(
                    "SELECT count(*) FROM listings WHERE last_seen_at < ? AND raw_json != '{}'",
                    (raw_cutoff.isoformat(),),
                ).fetchone()[0]
            )
            if apply and raw_rows:
                connection.execute(
                    "UPDATE listings SET raw_json='{}', raw_json_purged_at=? "
                    "WHERE last_seen_at < ? AND raw_json != '{}'",
                    (current.isoformat(), raw_cutoff.isoformat()),
                )
                connection.commit()
    expired = _expired_backups(backups, backup_cutoff, settings.backup_keep_count)
    stale, kept = _sidecars(backups, expired)
    if apply:
        for item in [*expired, *stale]:
            item.unlink(missing_ok=True)
        for database_file in kept:
            _fold_sidecars(database_file)
    return RetentionResult(
        apply=apply,
        raw_rows=raw_rows,
        backup_files=len(expired),
        sidecar_files=len(stale) + sum(len(_sidecar_files(item)) for item in kept),
        raw_cutoff=raw_cutoff.isoformat(),
        backup_cutoff=backup_cutoff.isoformat(),
    )


def backup_database(
    settings: Settings,
    *,
    destination: Path | None = None,
    now: datetime | None = None,
    backup_dir: Path | None = None,
) -> Path:
    source = sqlite_path(settings)
    if not source.is_file():
        raise FileNotFoundError(source)
    current = now or datetime.now(UTC)
    backups = (backup_dir or backup_directory(settings)).resolve()
    backups.mkdir(parents=True, exist_ok=True)
    target = (destination or backups / f"grailed-{current:%Y%m%dT%H%M%SZ}.sqlite3").resolve()
    if target.parent != backups:
        raise ValueError("Backups must be written inside data/backups")
    _sqlite_backup(source, target)
    # The copy inherits WAL mode; DELETE mode keeps each backup a single file.
    _set_rollback_journal(target)
    _require_integrity(target)
    return target


def restore_database(settings: Settings, source: Path, *, apply: bool = False) -> dict[str, object]:
    backup = source.resolve()
    if not backup.is_file():
        raise FileNotFoundError(backup)
    _require_integrity(backup)
    database = sqlite_path(settings)
    if not apply:
        return {"apply": False, "source": str(backup), "target": str(database), "valid": True}
    _require_application_stopped(settings)
    safety = backup_database(settings)
    _sqlite_backup(backup, database)
    _require_integrity(database)
    return {
        "apply": True,
        "source": str(backup),
        "target": str(database),
        "safety_backup": str(safety),
        "valid": True,
    }


def vacuum_database(settings: Settings) -> dict[str, object]:
    """Rebuild the database file; only by hand, with the app stopped and room on disk."""

    database = sqlite_path(settings)
    if not database.is_file():
        raise FileNotFoundError(database)
    lock = SingleInstanceLock(settings.data_directory / "app.lock")
    try:
        lock.acquire()
    except RuntimeError as exc:
        raise RuntimeError("Stop the application before VACUUM") from exc
    try:
        before = _footprint(database)
        free = shutil.disk_usage(database.parent).free
        if free < VACUUM_FREE_FACTOR * before:
            raise RuntimeError(
                f"VACUUM needs free space of {VACUUM_FREE_FACTOR * before} bytes, {free} available"
            )
        started = time.perf_counter()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        duration = time.perf_counter() - started
        after = _footprint(database)
    finally:
        lock.release()
    return {
        "status": "ok",
        "database": str(database),
        "before_bytes": before,
        "after_bytes": after,
        "duration_s": round(duration, 2),
    }


def result_dict(result: RetentionResult) -> dict[str, object]:
    return asdict(result)


def _footprint(database: Path) -> int:
    wal = Path(f"{database}-wal")
    return database.stat().st_size + (wal.stat().st_size if wal.exists() else 0)


def _modified(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _expired_backups(backups: Path, cutoff: datetime, keep: int) -> list[Path]:
    """Backups older than the cutoff, and every one beyond the newest ``keep``."""

    if not backups.exists():
        return []
    newest_first = sorted(
        (item for item in backups.glob(BACKUP_GLOB) if item.is_file()),
        key=_modified,
        reverse=True,
    )
    return [
        item for rank, item in enumerate(newest_first) if rank >= keep or _modified(item) < cutoff
    ]


def _sidecar_files(database: Path) -> list[Path]:
    return [
        sidecar for suffix in SIDECAR_SUFFIXES if (sidecar := Path(f"{database}{suffix}")).is_file()
    ]


def _sidecars(backups: Path, expired: list[Path]) -> tuple[list[Path], list[Path]]:
    """Sidecars to delete (backup gone or expiring) and kept backups that still have some."""

    if not backups.exists():
        return [], []
    stale: list[Path] = []
    kept: set[Path] = set()
    gone = set(expired)
    for suffix in SIDECAR_SUFFIXES:
        for sidecar in backups.glob(f"{BACKUP_GLOB}{suffix}"):
            database = sidecar.with_name(sidecar.name.removesuffix(suffix))
            if database in gone or not database.is_file():
                stale.append(sidecar)
            else:
                kept.add(database)
    return stale, sorted(kept)


def _fold_sidecars(database: Path) -> None:
    # A kept backup's WAL may hold pages: checkpoint it into the file instead of deleting.
    try:
        _set_rollback_journal(database)
    except sqlite3.DatabaseError:
        structlog.get_logger(__name__).warning("backup_sidecars_kept", backup=database.name)


def _set_rollback_journal(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(source)) as source_db,
        closing(sqlite3.connect(target)) as target_db,
    ):
        source_db.backup(target_db)


def _require_integrity(database: Path) -> None:
    uri = f"file:{database.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise ValueError("SQLite integrity_check failed")


def _require_application_stopped(settings: Settings) -> None:
    marker = settings.data_directory / "app.pid"
    if not marker.exists():
        return
    try:
        pid = int(marker.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        marker.unlink(missing_ok=True)
        return
    raise RuntimeError("Stop the application before restoring the database")
