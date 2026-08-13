"""Async SQLAlchemy setup used by API dependencies and Alembic."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import PROJECT_ROOT, Settings, get_settings


def get_database_url(settings: Settings) -> str:
    """Resolve the default relative SQLite path against the repository root."""

    prefix = "sqlite+aiosqlite:///"
    if settings.database_url.startswith(prefix):
        raw_path = settings.database_url.removeprefix(prefix)
        database_path = Path(raw_path)
        if not database_path.is_absolute():
            return f"{prefix}{(PROJECT_ROOT / database_path).as_posix()}"
    return settings.database_url


@lru_cache
def get_engine() -> AsyncEngine:
    """Create the process-wide async engine without opening a connection yet."""

    return create_async_engine(get_database_url(get_settings()), pool_pre_ping=True)


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
