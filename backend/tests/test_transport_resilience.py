"""Source-independent tests for rate, retry, cache, breaker and proxy policies."""

from __future__ import annotations

import asyncio

import pytest

from app.services.transport.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from app.services.transport.hosts import HostRotator
from app.services.transport.protocols import HttpResponse
from app.services.transport.proxy_manager import ProxyManager, ProxyUnavailableError
from app.services.transport.rate_limiter import RateLimiter
from app.services.transport.resilience import retry_after_seconds, with_retry
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


def test_host_rotator_cycles_hosts() -> None:
    rotator = HostRotator(["dsn", "one", "two"])
    states = [rotator.current, rotator.rotate(), rotator.rotate(), rotator.rotate()]
    assert states == ["dsn", "one", "two", "dsn"]


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


@pytest.mark.asyncio
async def test_retry_honours_retry_after_without_real_sleep() -> None:
    calls = 0
    sleeps: list[float] = []

    async def operation() -> HttpResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return HttpResponse(429, {"retry-after": "3"}, b"", "https://example.test")
        return HttpResponse(200, {}, b"{}", "https://example.test")

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    result = await with_retry(operation, breaker=CircuitBreaker(), sleep=sleep)
    assert result.status_code == 200
    assert sleeps == [3.0]
    assert retry_after_seconds({"Retry-After": "invalid"}) is None


def test_proxy_manager_is_sticky_and_cools_down_failed_proxy() -> None:
    now = 0.0
    manager = ProxyManager(["http://one", "socks5://two"], now=lambda: now)
    selected = manager.select("brand-1")
    assert manager.select("brand-1") == selected
    assert selected is not None
    for _ in range(3):
        manager.record_failure(selected)
    assert manager.select("brand-1") != selected


def test_proxy_manager_can_refuse_direct_fallback() -> None:
    manager = ProxyManager([], allow_direct_fallback=False)
    with pytest.raises(ProxyUnavailableError):
        manager.select("brand-1")


def test_proxy_manager_chooses_from_requested_pool() -> None:
    manager = ProxyManager(
        http_proxies=["http://http-proxy"], browser_proxies=["socks5://browser-proxy"]
    )

    assert manager.select("http-session", pool="http") == "http://http-proxy"
    assert manager.select("browser-session", pool="browser") == "socks5://browser-proxy"


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


@pytest.mark.asyncio
async def test_proxy_health_test_masks_credentials_and_records_result() -> None:
    manager = ProxyManager(["http://user:password@proxy.test:50100"])

    statuses = await manager.test_all(lambda _: _healthy_proxy())

    assert statuses[0]["proxy"] == "http://***:***@proxy.test:50100"
    assert statuses[0]["successes"] == 1
    assert "password" not in str(statuses)


async def _healthy_proxy() -> bool:
    return True
