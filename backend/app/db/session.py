"""Async SQLAlchemy setup used by API dependencies and Alembic."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import PROJECT_ROOT, Settings, get_settings

# A long read keeps the WAL from being reset; after a checkpoint it shrinks back to this size.
JOURNAL_SIZE_LIMIT_BYTES = 64 * 1024 * 1024


def get_database_url(settings: Settings) -> str:
    """Resolve the default relative SQLite path against the repository root."""

    prefix = "sqlite+aiosqlite:///"
    if settings.database_url.startswith(prefix):
        raw_path = settings.database_url.removeprefix(prefix)
        database_path = Path(raw_path)
        if not database_path.is_absolute():
            return f"{prefix}{(PROJECT_ROOT / database_path).as_posix()}"
    return settings.database_url


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create the configured engine and apply SQLite safety settings per connection."""

    engine = create_async_engine(get_database_url(settings), pool_pre_ping=True)
    if engine.url.get_backend_name() == "sqlite":
        timeout = settings.sqlite_busy_timeout_ms

        def configure_sqlite(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={timeout}")
                cursor.execute(f"PRAGMA journal_size_limit={JOURNAL_SIZE_LIMIT_BYTES}")
            finally:
                cursor.close()

        event.listen(engine.sync_engine, "connect", configure_sqlite)
    return engine


async def checkpoint_database(session: AsyncSession) -> dict[str, int]:
    """Fold the WAL back into the database and refresh planner statistics.

    Run after a commit, outside a transaction. ``busy`` is 1 when a reader kept the WAL from
    being truncated; the next checkpoint catches up, so it is reported, not raised.
    """

    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        return {"busy": 0, "wal_pages": 0, "checkpointed_pages": 0}
    # ``optimize`` may run ANALYZE, which writes: it goes first so the checkpoint takes it too.
    await session.execute(text("PRAGMA optimize"))
    await session.commit()
    row = (await session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))).one()
    await session.commit()
    return {"busy": int(row[0]), "wal_pages": int(row[1]), "checkpointed_pages": int(row[2])}


@lru_cache
def get_engine() -> AsyncEngine:
    """Create the process-wide async engine without opening a connection yet."""

    return create_database_engine(get_settings())


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Create the process-wide factory for short-lived request sessions."""

    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Provide a transaction-neutral database session for one request."""

    async with get_session_factory()() as session:
        yield session


async def close_database() -> None:
    """Release pool resources at application shutdown."""

    if get_engine.cache_info().currsize:
        await get_engine().dispose()
        get_session_factory.cache_clear()
        get_engine.cache_clear()
