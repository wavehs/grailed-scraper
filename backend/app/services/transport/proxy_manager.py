"""Proxy selection with sticky source sessions and conservative health scoring."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(slots=True)
class ProxyHealth:
    url: str
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0

    @property
    def score(self) -> float:
        return (self.successes + 1) / (self.successes + self.failures + 2)

    def public_status(self, now: float) -> dict[str, float | int | str | bool | None]:
        """Return health data without exposing proxy credentials."""

        parts = urlsplit(self.url)
        host = parts.hostname or "invalid"
        port = f":{parts.port}" if parts.port else ""
        return {
            "proxy": f"{parts.scheme}://***:***@{host}{port}",
            "success_rate": self.score,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "cooling_down": self.cooldown_until > now,
            "cooldown_remaining_s": max(0.0, self.cooldown_until - now) or None,
        }


class ProxyUnavailableError(RuntimeError):
    """Raised when direct fallback is disabled and no healthy proxy remains."""


class ProxyManager:
    def __init__(
        self,
        proxies: list[str] | None = None,
        *,
        http_proxies: list[str] | None = None,
        browser_proxies: list[str] | None = None,
        allow_direct_fallback: bool = True,
        cooldown_s: float = 600.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        shared = list(proxies or [])
        http_pool = list(http_proxies) if http_proxies is not None else shared
        browser_pool = list(browser_proxies) if browser_proxies is not None else shared
        configured = [*http_pool, *browser_pool]
        for proxy in configured:
            if urlsplit(proxy).scheme not in {"http", "https", "socks5"}:
                raise ValueError("Proxy must use http, https, or socks5")
        self._health = [ProxyHealth(url=proxy) for proxy in dict.fromkeys(configured)]
        self._pool_urls = {"http": set(http_pool), "browser": set(browser_pool)}
        self._sticky: dict[str, ProxyHealth | None] = {}
        self._allow_direct_fallback = allow_direct_fallback
        self._cooldown_s = cooldown_s
        self._now = now

    def select(self, session_key: str, *, pool: str = "http") -> str | None:
        """Choose a sticky proxy for a source session.

        The first pool to select for a session pins the proxy for both HTTP and
        browser calls, preventing an in-run change of apparent geography.
        """

        if pool not in {"http", "browser"}:
            raise ValueError("pool must be 'http' or 'browser'")
        if session_key in self._sticky:
            selected = self._sticky[session_key]
            if selected is None or selected.cooldown_until <= self._now():
                return selected.url if selected else None
        candidates = [
            item
            for item in self._health
            if item.url in self._pool_urls[pool] and item.cooldown_until <= self._now()
        ]
        if not candidates:
            if not self._allow_direct_fallback:
                raise ProxyUnavailableError("All proxies are in cooldown")
            self._sticky[session_key] = None
            return None
        selected = random.choices(candidates, weights=[item.score for item in candidates], k=1)[0]
        self._sticky[session_key] = selected
        return selected.url

    def record_success(self, proxy_url: str) -> None:
        health = self._find(proxy_url)
        health.successes += 1
        health.consecutive_failures = 0

    def record_failure(self, proxy_url: str) -> None:
        health = self._find(proxy_url)
        health.failures += 1
        health.consecutive_failures += 1
        if health.consecutive_failures >= 3:
            health.cooldown_until = self._now() + self._cooldown_s
            health.consecutive_failures = 0
            self._sticky = {
                key: value for key, value in self._sticky.items() if value is not health
            }

    def statuses(self) -> list[dict[str, float | int | str | bool | None]]:
        """Expose health summaries suitable for API responses and logs."""

        now = self._now()
        return [item.public_status(now) for item in self._health]

    async def test_all(
        self,
        probe: Callable[[str], Awaitable[bool]],
        *,
        max_concurrency: int = 3,
    ) -> list[dict[str, float | int | str | bool | None]]:
        """Probe each configured proxy with bounded concurrency and update health."""

        semaphore = asyncio.Semaphore(max_concurrency)

        async def test(proxy_url: str) -> None:
            async with semaphore:
                try:
                    healthy = await probe(proxy_url)
                except Exception:
                    healthy = False
                if healthy:
                    self.record_success(proxy_url)
                else:
                    self.record_failure(proxy_url)

        await asyncio.gather(*(test(item.url) for item in self._health))
        return self.statuses()

    def _find(self, proxy_url: str) -> ProxyHealth:
        for health in self._health:
            if health.url == proxy_url:
                return health
        raise KeyError(proxy_url)
