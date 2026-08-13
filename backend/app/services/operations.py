"""Safe local SQLite backup, restore, and raw-data retention operations."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings
from app.db.session import get_database_url


@dataclass(frozen=True, slots=True)
class RetentionResult:
    apply: bool
    raw_rows: int
    backup_files: int
    raw_cutoff: str
    backup_cutoff: str


def sqlite_path(settings: Settings) -> Path:
    prefix = "sqlite+aiosqlite:///"
    url = get_database_url(settings)
    if not url.startswith(prefix) or url.endswith(":memory:"):
        raise ValueError("This operation requires a file-backed SQLite database")
    return Path(url.removeprefix(prefix)).resolve()


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
    backups = (backup_dir or PROJECT_ROOT / "data" / "backups").resolve()
    raw_rows = 0
    if database.exists():
        with sqlite3.connect(database) as connection:
            raw_rows = int(
                connection.execute(
                    "SELECT count(*) FROM listings "
                    "WHERE last_seen_at < ? AND raw_json != '{}'",
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
    expired = [
        item
        for item in backups.glob("*.sqlite3")
        if item.is_file()
        and datetime.fromtimestamp(item.stat().st_mtime, tz=UTC) < backup_cutoff
    ] if backups.exists() else []
    if apply:
        for item in expired:
            item.unlink()
    return RetentionResult(
        apply=apply,
        raw_rows=raw_rows,
        backup_files=len(expired),
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
    backups = (backup_dir or PROJECT_ROOT / "data" / "backups").resolve()
    backups.mkdir(parents=True, exist_ok=True)
    target = (destination or backups / f"grailed-{current:%Y%m%dT%H%M%SZ}.sqlite3").resolve()
    if target.parent != backups:
        raise ValueError("Backups must be written inside data/backups")
    _sqlite_backup(source, target)
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
    _require_application_stopped()
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


def result_dict(result: RetentionResult) -> dict[str, object]:
    return asdict(result)


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
        source_db.backup(target_db)


def _require_integrity(database: Path) -> None:
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise ValueError("SQLite integrity_check failed")


def _require_application_stopped() -> None:
    marker = PROJECT_ROOT / "data" / "app.pid"
    if not marker.exists():
        return
    try:
        pid = int(marker.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        marker.unlink(missing_ok=True)
        return
    raise RuntimeError("Stop the application before restoring the database")
