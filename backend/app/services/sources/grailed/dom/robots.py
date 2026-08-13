"""Cached robots.txt enforcement for all Tier-three navigation."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from app.services.transport.protocols import HttpTransport


class RobotsDenied(RuntimeError):
    pass


class RobotsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _CachedRules:
    expires_at: float
    parser: RobotFileParser


class RobotsPolicy:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        ttl_s: float = 24 * 60 * 60,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._ttl_s = ttl_s
        self._now = now
        self._cache: dict[str, _CachedRules] = {}

    async def require_allowed(self, url: str, *, user_agent: str = "*") -> None:
        split = urlsplit(url)
        origin = f"{split.scheme}://{split.netloc}"
        cached = self._cache.get(origin)
        if cached is None or cached.expires_at <= self._now():
            response = await self._transport.request("GET", f"{origin}/robots.txt")
            if response.status_code == 404:
                text = "User-agent: *\nAllow: /"
            elif response.status_code != 200:
                raise RobotsUnavailable(
                    f"robots.txt could not be verified (HTTP {response.status_code})"
                )
            else:
                text = response.text
            parser = RobotFileParser()
            parser.set_url(f"{origin}/robots.txt")
            parser.parse(text.splitlines())
            cached = _CachedRules(self._now() + self._ttl_s, parser)
            self._cache[origin] = cached
        if not cached.parser.can_fetch(user_agent, url):
            raise RobotsDenied("Tier 3 path is disallowed by robots.txt")
