"""Per-endpoint circuit breaker that prevents repeated failed traffic."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a request is rejected by an open circuit."""


@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int = 5
    window_s: float = 60.0
    recovery_s: float = 120.0
    _failures: deque[float] = field(default_factory=deque)
    _opened_at: float | None = None
    _probe_active: bool = False

    @property
    def state(self) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        if time.monotonic() - self._opened_at >= self.recovery_s:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def allow_request(self) -> None:
        state = self.state
        if state is CircuitState.OPEN:
            raise CircuitOpenError("Circuit is open")
        if state is CircuitState.HALF_OPEN:
            if self._probe_active:
                raise CircuitOpenError("Half-open circuit already has a probe")
            self._probe_active = True

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None
        self._probe_active = False

    def record_failure(self) -> None:
        now = time.monotonic()
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.window_s:
            self._failures.popleft()
        if self._probe_active or len(self._failures) >= self.failure_threshold:
            self._opened_at = now
        self._probe_active = False
