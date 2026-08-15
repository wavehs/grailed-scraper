from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.services.transport.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from app.services.transport.protocols import HttpResponse
from app.services.transport.proxy_manager import ProxyManager, ProxyUnavailableError
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
