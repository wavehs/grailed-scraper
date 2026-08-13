"""Health-aware Algolia read-host selection."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable


def algolia_hosts(app_id: str) -> tuple[str, ...]:
    return (
        f"https://{app_id}-dsn.algolia.net",
        f"https://{app_id}-1.algolianet.com",
        f"https://{app_id}-2.algolianet.com",
        f"https://{app_id}-3.algolianet.com",
    )


class AlgoliaHostPool:
    def __init__(
        self,
        hosts: Iterable[str],
        *,
        cooldown_s: float = 300.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hosts = tuple(hosts)
        if not self._hosts:
            raise ValueError("At least one Algolia host is required")
        self._cooldown_s = cooldown_s
        self._now = now
        self._down_until: dict[str, float] = {}

    def candidates(self) -> tuple[str, ...]:
        now = self._now()
        healthy = tuple(host for host in self._hosts if self._down_until.get(host, 0) <= now)
        return healthy or self._hosts

    def mark_down(self, host: str) -> None:
        self._down_until[host] = self._now() + self._cooldown_s
