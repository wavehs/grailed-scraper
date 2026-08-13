"""Retries that cooperate with rate limiting and circuit breakers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import TypeVar

from app.services.transport.circuit_breaker import CircuitBreaker
from app.services.transport.protocols import HttpResponse

T = TypeVar("T")
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def retry_after_seconds(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value).timestamp()
            return max(0.0, float(target - time.time()))
        except (TypeError, ValueError):
            return None


async def with_retry(
    operation: Callable[[], Awaitable[HttpResponse]],
    *,
    breaker: CircuitBreaker,
    max_retries: int = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> HttpResponse:
    """Retry transient status codes and open the breaker on final failure."""

    for attempt in range(max_retries + 1):
        breaker.allow_request()
        try:
            response = await operation()
        except Exception:
            if attempt == max_retries:
                breaker.record_failure()
                raise
            await sleep(min(2**attempt, 30.0))
            continue
        if response.status_code not in RETRYABLE_STATUS_CODES:
            breaker.record_success()
            return response
        if attempt == max_retries:
            breaker.record_failure()
            return response
        await sleep(retry_after_seconds(response.headers) or min(2**attempt, 30.0))
    raise AssertionError("unreachable")
