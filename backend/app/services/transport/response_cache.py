"""Small in-memory TTL cache for repeatable development requests."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from app.services.transport.protocols import HttpResponse


@dataclass(frozen=True, slots=True)
class CachedResponse:
    expires_at: float
    response: HttpResponse


class ResponseCache:
    def __init__(self, ttl_s: float = 60.0, max_entries: int = 32) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._entries: dict[str, CachedResponse] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(method: str, url: str, params: dict[str, str] | None, json_body: Any | None) -> str:
        payload = json.dumps([method, url, params or {}, json_body], sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, key: str) -> HttpResponse | None:
        entry = self._entries.get(key)
        if entry is None or entry.expires_at <= time.monotonic():
            self._entries.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        return entry.response

    def set(self, key: str, response: HttpResponse) -> None:
        while len(self._entries) >= self._max_entries and key not in self._entries:
            self._entries.pop(next(iter(self._entries)))
        self._entries[key] = CachedResponse(time.monotonic() + self._ttl_s, response)
