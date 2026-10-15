"""Retry API writes that lost the SQLite write lock to the parser or a regroup.

``busy_timeout`` already waits inside SQLite; a regroup of a large brand can hold the lock
longer than that. A write that still fails is re-run from scratch: every attempt gets fresh
dependencies (its own session), and the failed attempt's session is closed before the pause.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import AsyncExitStack
from typing import Any, TypeVar

import structlog
from fastapi import Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError

MUTATION_ATTEMPTS = 3
MUTATION_BACKOFF_S = 0.5
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# FastAPI closes yield dependencies through these stacks; each attempt gets its own.
_STACK_KEYS = ("fastapi_inner_astack", "fastapi_function_astack")

Handler = Callable[[Request], Coroutine[Any, Any, Response]]
T = TypeVar("T")


def is_database_locked(error: OperationalError) -> bool:
    return "database is locked" in str(error.orig)


async def _back_off(attempt: int, where: str) -> None:
    structlog.get_logger(__name__).warning("database_locked_retry", where=where, attempt=attempt)
    await asyncio.sleep(MUTATION_BACKOFF_S * 2 ** (attempt - 1))


async def retry_locked(
    session: AsyncSession, operation: Callable[[], Awaitable[T]], *, where: str
) -> T:
    """Run ``operation`` again (after a rollback) while it loses the write lock.

    For work after a commit inside a handler: re-running the whole handler would repeat
    the already durable part. The last ``database is locked`` error is re-raised.
    """

    for attempt in range(1, MUTATION_ATTEMPTS + 1):
        try:
            return await operation()
        except OperationalError as error:
            await session.rollback()
            if not is_database_locked(error) or attempt == MUTATION_ATTEMPTS:
                raise
            await _back_off(attempt, where)
    raise AssertionError("unreachable")


async def _attempt(handler: Handler, request: Request) -> Response:
    scope = request.scope
    saved = {key: scope.get(key) for key in _STACK_KEYS}
    try:
        async with AsyncExitStack() as stack:
            for key in _STACK_KEYS:
                scope[key] = stack
            return await handler(request)
    finally:
        scope.update(saved)


class MutationRoute(APIRoute):
    """An API route whose writes are retried on ``database is locked``."""

    def get_route_handler(self) -> Handler:
        handler = super().get_route_handler()

        async def retrying(request: Request) -> Response:
            if request.method in _READ_METHODS:
                return await handler(request)
            for attempt in range(1, MUTATION_ATTEMPTS + 1):
                try:
                    return await _attempt(handler, request)
                except OperationalError as error:
                    if not is_database_locked(error):
                        raise
                    if attempt == MUTATION_ATTEMPTS:
                        raise ApiError(
                            503, "database_locked", "The database is busy, try again shortly"
                        ) from error
                    await _back_off(attempt, request.url.path)
            raise AssertionError("unreachable")

        return retrying
