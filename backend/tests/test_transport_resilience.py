from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.services.transport.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from app.services.transport.protocols import HttpResponse
from app.services.transport.rate_limiter import RateLimiter
from app.services.transport.resilience import retry_after_seconds
from app.services.transport.response_cache import ResponseCache


def test_response_cache_uses_stable_request_key() -> None:
    cache = ResponseCache(max_entries=2)
    key = cache.key("POST", "https://example.test", {"b": "2"}, {"a": 1})
    response = HttpResponse(200, {}, b"{}", "https://example.test")
    cache.set(key, response)
    assert cache.get(key) == response
    second = cache.key("POST", "https://example.test/2", None, None)
    third = cache.key("POST", "https://example.test/3", None, None)
    cache.set(second, response)
    cache.set(third, response)
    assert cache.get(key) is None
    assert cache.get(second) == response


def test_circuit_opens_then_allows_one_half_open_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 0.0
    monkeypatch.setattr("app.services.transport.circuit_breaker.time.monotonic", lambda: now)
    breaker = CircuitBreaker(failure_threshold=2, recovery_s=10)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.allow_request()
    now = 11.0
    breaker.allow_request()
    with pytest.raises(CircuitOpenError):
        breaker.allow_request()
    breaker.record_success()
    assert breaker.state.value == "closed"


def test_retry_after_seconds_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert retry_after_seconds({"retry-after": "3"}) == 3.0
    assert retry_after_seconds({"Retry-After": "10.5"}) == 10.5
    assert retry_after_seconds({"Retry-After": "invalid"}) is None
    assert retry_after_seconds({}) is None

    # Test HTTP-date format
    monkeypatch.setattr("app.services.transport.resilience.time.time", lambda: 1000.0)
    # Wed, 21 Oct 2015 07:28:00 GMT = 1445412480
    date_str = datetime.fromtimestamp(1015.0, tz=UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert retry_after_seconds({"retry-after": date_str}) == 15.0


@pytest.mark.asyncio
async def test_rate_limiter_caps_same_host_concurrency() -> None:
    limiter = RateLimiter(requests_per_minute=60_000, max_concurrent_per_host=1, jitter_ratio=0)
    active = 0
    maximum = 0

    async def worker() -> None:
        nonlocal active, maximum
        async with limiter.limit("https://algolia.test/query"):
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1

    await asyncio.gather(worker(), worker())
    assert maximum == 1
