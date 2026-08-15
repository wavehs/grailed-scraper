"""Release identity and production startup checks."""

from __future__ import annotations

import json
import msvcrt
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, BinaryIO

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import PROJECT_ROOT, Settings
from app.db.session import get_database_url


class SingleInstanceLock:
    """Hold one byte of a Windows lock file for the backend process lifetime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+b")
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            lock_file.close()
            raise RuntimeError("another_backend_instance_is_running") from exc
        self._file = lock_file

    def status(self) -> dict[str, Any]:
        return {
            "status": "held" if self._file is not None else "released",
            "pid": os.getpid() if self._file is not None else None,
        }

    def release(self) -> None:
        if self._file is None:
            return
        self._file.seek(0)
        msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        self._file.close()
        self._file = None


def resolve_revision(explicit: str | None = None) -> str:
    """Resolve the running revision without exposing command output or environment data."""

    if explicit and explicit.strip():
        return explicit.strip()
    metadata = PROJECT_ROOT / "data" / "release.json"
    try:
        revision = json.loads(metadata.read_text(encoding="utf-8")).get("revision")
        if isinstance(revision, str) and revision.strip():
            return revision.strip()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    git_bin = shutil.which("git")
    if not git_bin and os.name == "nt":
        for candidate in (
            Path(r"C:\Program Files\Git\cmd\git.exe"),
            Path(r"C:\Program Files\Git\bin\git.exe"),
        ):
            if candidate.exists():
                git_bin = str(candidate)
                break
    if not git_bin:
        git_bin = "git"
    try:
        return (
            subprocess.run(
                [git_bin, "rev-parse", "HEAD"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
            or "unknown"
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"


def validate_production(settings: Settings, revision: str) -> list[str]:
    """Return stable reason codes for unsafe production configuration."""

    if settings.environment != "production":
        return []
    reasons: list[str] = []
    if revision == "unknown":
        reasons.append("revision_unknown")
    loopback_hosts = {"localhost", "127.0.0.1", "::1", "[::1]"}
    if settings.backend_bind_host not in loopback_hosts:
        reasons.append("backend_bind_not_loopback")
    if settings.frontend_bind_host not in loopback_hosts:
        reasons.append("frontend_bind_not_loopback")
    if settings.cors_origins != ["http://127.0.0.1:3000"]:
        reasons.append("frontend_origin_not_local")
    return reasons


async def inspect_runtime(settings: Settings, engine: AsyncEngine) -> dict[str, Any]:
    """Inspect startup-critical state without applying migrations."""

    revision = resolve_revision(settings.revision)
    production_reasons = validate_production(settings, revision)
    schema = await _inspect_schema(settings, engine)
    return {
        "revision": revision,
        "environment": settings.environment,
        "schema": schema,
        "directories": {
            "data": _directory_status(settings.data_directory),
            "logs": _directory_status(settings.log_directory),
        },
        "single_instance_lock": {"status": "not_acquired"},
        "production": {
            "status": "invalid" if production_reasons else "ready",
            "reasons": production_reasons,
            "backend_bind_host": settings.backend_bind_host,
            "frontend_bind_host": settings.frontend_bind_host,
        },
    }


def require_startup_ready(settings: Settings, report: dict[str, Any]) -> None:
    """Fail production before API/parser startup when the runtime is unsafe."""

    if settings.environment != "production":
        return
    reasons = list(report["production"]["reasons"])
    if report["schema"]["status"] != "current":
        reasons.append(f"database_schema_{report['schema']['status']}")
    if report["single_instance_lock"]["status"] != "held":
        reasons.append("single_instance_lock_not_held")
    unavailable = [name for name, status in report["directories"].items() if not status["writable"]]
    reasons.extend(f"{name}_directory_unwritable" for name in unavailable)
    if reasons:
        raise RuntimeError(f"Production startup blocked: {', '.join(reasons)}")


async def _inspect_schema(settings: Settings, engine: AsyncEngine) -> dict[str, Any]:
    config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "alembic"))
    heads = sorted(ScriptDirectory.from_config(config).get_heads())
    database_url = get_database_url(settings)
    if database_url.startswith("sqlite+aiosqlite:///"):
        database_path = Path(database_url.removeprefix("sqlite+aiosqlite:///"))
        if not database_path.exists():
            return {"status": "missing", "current": [], "heads": heads}
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            current = sorted(str(value) for value in result.scalars())
    except SQLAlchemyError:
        return {"status": "unknown", "current": [], "heads": heads}
    return {
        "status": "current" if current == heads else "outdated",
        "current": current,
        "heads": heads,
    }


def _directory_status(path: Path) -> dict[str, bool]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return {"exists": path.is_dir(), "writable": os.access(path, os.W_OK)}
    except OSError:
        return {"exists": False, "writable": False}
