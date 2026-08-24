"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.errors import install_exception_handlers
from app.api.routes import router
from app.core.config import PROJECT_ROOT, RESOURCE_ROOT, get_settings
from app.core.logging import configure_logging
from app.core.request_context import RequestIdMiddleware
from app.core.runtime import SingleInstanceLock, inspect_runtime, require_startup_ready
from app.db.session import close_database, get_engine, get_session_factory
from app.services.parser.runtime import ParserRuntime
from app.services.transport.capabilities import probe_capabilities


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Configure process resources once and dispose them predictably."""

    settings = get_settings()
    configure_logging(settings)
    structlog.get_logger(__name__).info("parser_capabilities", **probe_capabilities().as_dict())
    instance_lock = SingleInstanceLock(settings.data_directory / "app.lock")
    instance_lock.acquire()
    pid_file = settings.data_directory / "app.pid"
    runtime: ParserRuntime | None = None
    try:
        from app.db.models import Base

        async with get_engine().begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        runtime_report = await inspect_runtime(settings, get_engine())
        runtime_report["single_instance_lock"] = instance_lock.status()
        require_startup_ready(settings, runtime_report)
        application.state.runtime_report = runtime_report
        runtime = ParserRuntime(get_session_factory(), settings)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="ascii")
        application.state.parser_runtime = runtime
        try:
            await runtime.reconcile()
        except SQLAlchemyError:
            structlog.get_logger(__name__).warning("parser_reconcile_skipped")
        yield
    finally:
        try:
            if runtime is not None:
                await runtime.close()
            await close_database()
            await _remove_own_pid_file(pid_file, os.getpid())
        finally:
            instance_lock.release()


async def _remove_own_pid_file(pid_file: Path, process_id: int) -> None:
    """Remove our PID marker without deleting a marker replaced by another process."""

    for attempt in range(4):
        try:
            if not pid_file.exists():
                return
            if pid_file.read_text(encoding="ascii").strip() != str(process_id):
                return
            pid_file.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 3:
                structlog.get_logger(__name__).warning(
                    "pid_file_cleanup_deferred", path=str(pid_file)
                )
                return
            await asyncio.sleep(0.05 * (attempt + 1))




def _find_static_directory() -> Path | None:
    """Locate the exported Next.js static assets directory if present."""
    meipass = getattr(sys, "_MEIPASS", None)
    candidates = [
        Path(meipass) / "static" if meipass else None,
        RESOURCE_ROOT / "static",
        RESOURCE_ROOT / "frontend" / "out",
        PROJECT_ROOT / "frontend" / "out",
        PROJECT_ROOT / "static",
        Path(__file__).resolve().parent / "static",
    ]
    for candidate in candidates:
        if candidate and candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    return None


def mount_static_frontend(application: FastAPI) -> None:
    """Mount compiled frontend static files with SPA fallback."""
    static_dir = _find_static_directory()
    if static_dir is None:
        return

    next_static = static_dir / "_next"
    if next_static.is_dir():
        application.mount("/_next", StaticFiles(directory=str(next_static)), name="next_static")

    @application.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa_frontend(full_path: str) -> Response:
        from starlette.exceptions import HTTPException

        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404)

        target = static_dir / full_path
        if full_path and target.is_file():
            return FileResponse(target)

        html_target = static_dir / f"{full_path}.html"
        if full_path and html_target.is_file():
            return FileResponse(html_target)

        dir_index = target / "index.html"
        if target.is_dir() and dir_index.is_file():
            return FileResponse(dir_index)

        if full_path.startswith("model-groups"):
            model_group_page = static_dir / "model-groups.html"
            if model_group_page.is_file():
                return FileResponse(model_group_page)
            model_group_1_page = static_dir / "model-groups" / "1.html"
            if model_group_1_page.is_file():
                return FileResponse(model_group_1_page)

        index_file = static_dir / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)

        raise HTTPException(status_code=404)


application_settings = get_settings()
app = FastAPI(title="Grailed Liquidity Analyzer", version=__version__, lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "[::1]"]
    + (["testserver"] if application_settings.environment != "production" else []),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=application_settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID"],
)
app.add_middleware(RequestIdMiddleware)
app.include_router(router)
install_exception_handlers(app)
mount_static_frontend(app)
