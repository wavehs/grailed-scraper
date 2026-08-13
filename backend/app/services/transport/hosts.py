"""Deterministic failover over Algolia-compatible host candidates."""

from __future__ import annotations

from collections.abc import Sequence


class HostRotator:
    def __init__(self, hosts: Sequence[str]) -> None:
        if not hosts:
            raise ValueError("At least one host is required")
        self._hosts = tuple(hosts)
        self._index = 0

    @property
    def current(self) -> str:
        return self._hosts[self._index]

    def rotate(self) -> str:
        self._index = (self._index + 1) % len(self._hosts)
        return self.current
