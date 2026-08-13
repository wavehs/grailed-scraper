"""Polite global rate limiting with per-host concurrency protection."""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit


class RateLimiter:
    """Token bucket shared by a source plus lazily-created host semaphores."""

    def __init__(
        self,
        requests_per_minute: int = 90,
        max_concurrent_per_host: int = 3,
        burst: int = 5,
        jitter_ratio: float = 0.3,
    ) -> None:
        self._rate = requests_per_minute / 60
        self._burst = float(burst)
        self._tokens = float(burst)
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()
        self._host_limit = max_concurrent_per_host
        self._semaphores: dict[str, asyncio.Semaphore] = defaultdict(self._new_semaphore)
        self._jitter_ratio = jitter_ratio

    def _new_semaphore(self) -> asyncio.Semaphore:
        return asyncio.Semaphore(self._host_limit)

    async def acquire(self, url: str) -> None:
        """Wait for a global token then acquire the host's concurrency slot."""

        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                self._updated_at = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    break
                wait_s = (1 - self._tokens) / self._rate
            await asyncio.sleep(wait_s)
        await self._semaphores[urlsplit(url).netloc].acquire()

    def release(self, url: str) -> None:
        self._semaphores[urlsplit(url).netloc].release()

    @asynccontextmanager
    async def limit(self, url: str) -> AsyncIterator[None]:
        await self.acquire(url)
        try:
            if self._jitter_ratio:
                delay = (1 / self._rate) * random.uniform(0, self._jitter_ratio)
                await asyncio.sleep(delay)
            yield
        finally:
            self.release(url)
