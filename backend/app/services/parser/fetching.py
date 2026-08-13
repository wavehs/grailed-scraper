"""Tier state machine presented as one resumable Algolia read client."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from app.domain.listings import FetchTier
from app.services.parser.observability import RunMetrics
from app.services.sources.grailed.algolia.exceptions import (
    AlgoliaAuthError,
    AlgoliaError,
    AlgoliaRateLimited,
    WafChallenge,
)
from app.services.sources.grailed.algolia.models import (
    AlgoliaPage,
    AlgoliaQuery,
    AlgoliaRequest,
    FacetValue,
)
from app.services.transport.circuit_breaker import CircuitOpenError

T = TypeVar("T")


class FetchApi(Protocol):
    async def search(self, index_name: str, query: AlgoliaQuery) -> AlgoliaPage: ...

    async def multi_query(
        self, requests: Sequence[AlgoliaRequest]
    ) -> tuple[AlgoliaPage, ...]: ...

    async def browse(
        self, index_name: str, query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage: ...

    async def search_facet_values(
        self,
        index_name: str,
        facet_name: str,
        facet_query: str,
        *,
        query: AlgoliaQuery | None = None,
    ) -> tuple[FacetValue, ...]: ...


@dataclass(frozen=True, slots=True)
class TierTransition:
    previous: FetchTier
    current: FetchTier
    reason: str


class TieredFetcher:
    def __init__(
        self,
        clients: dict[FetchTier, FetchApi],
        *,
        source_mode: str = "live",
        preferred: FetchTier = "T1",
        refresh_credentials: Callable[[], Awaitable[None]] | None = None,
        canary: Callable[[], Awaitable[bool]] | None = None,
        canary_interval_s: float = 300.0,
        now: Callable[[], float] = time.monotonic,
        metrics: RunMetrics | None = None,
    ) -> None:
        initial: FetchTier = "T0" if source_mode in {"mock", "replay"} else preferred
        if initial not in clients:
            raise ValueError(f"No client configured for initial tier {initial}")
        self._clients = clients
        self._current = initial
        self._refresh_credentials = refresh_credentials
        self._canary = canary
        self._canary_interval_s = canary_interval_s
        self._now = now
        self._last_canary = now()
        self._canary_successes = 0
        self._auth_failures = 0
        self._rate_failures = 0
        self._waf_failures = 0
        self._t2_failures = 0
        self.transitions: list[TierTransition] = []
        self._metrics = metrics

    @property
    def current_tier(self) -> FetchTier:
        return self._current

    @property
    def degraded_mode(self) -> bool:
        return any(transition.current in {"T2", "T3"} for transition in self.transitions)

    async def search(self, index_name: str, query: AlgoliaQuery) -> AlgoliaPage:
        return await self._invoke(lambda client: client.search(index_name, query))

    async def multi_query(
        self, requests: Sequence[AlgoliaRequest]
    ) -> tuple[AlgoliaPage, ...]:
        return await self._invoke(lambda client: client.multi_query(requests))

    async def browse(
        self, index_name: str, query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage:
        return await self._invoke(
            lambda client: client.browse(index_name, query, cursor=cursor)
        )

    async def search_facet_values(
        self,
        index_name: str,
        facet_name: str,
        facet_query: str,
        *,
        query: AlgoliaQuery | None = None,
    ) -> tuple[FacetValue, ...]:
        return await self._invoke(
            lambda client: client.search_facet_values(
                index_name, facet_name, facet_query, query=query
            )
        )

    async def _invoke(self, operation: Callable[[FetchApi], Awaitable[T]]) -> T:
        await self._maybe_canary()
        while True:
            client = self._clients[self._current]
            tier = self._current
            started = time.perf_counter()
            try:
                result = await operation(client)
            except (AlgoliaError, CircuitOpenError) as exc:
                if self._metrics is not None and tier in {"T2", "T3"}:
                    self._metrics.record_response(
                        tier,
                        getattr(exc, "status_code", 599),
                        (time.perf_counter() - started) * 1000,
                    )
                if self._current == "T0" or self._current == "T3":
                    raise
                if self._current == "T2":
                    self._t2_failures += 1
                    if self._t2_failures >= 2:
                        self._transition(self._next_available("T3"), type(exc).__name__)
                    continue
                await self._handle_t1_failure(exc)
                continue
            self._reset_failures(self._current)
            if self._metrics is not None and tier in {"T2", "T3"}:
                self._metrics.record_response(tier, 200, (time.perf_counter() - started) * 1000)
            return result

    async def _handle_t1_failure(self, exc: AlgoliaError | CircuitOpenError) -> None:
        threshold = 1
        reason = type(exc).__name__
        if isinstance(exc, AlgoliaAuthError):
            if self._refresh_credentials is not None:
                await self._refresh_credentials()
            self._auth_failures += 1
            threshold = 3
            count = self._auth_failures
        elif isinstance(exc, AlgoliaRateLimited):
            self._rate_failures += 1
            threshold = 5
            count = self._rate_failures
        elif isinstance(exc, WafChallenge):
            self._waf_failures += 1
            threshold = 3
            count = self._waf_failures
        else:
            count = threshold
        if count >= threshold:
            self._transition(self._next_available("T2"), reason)

    async def _maybe_canary(self) -> None:
        if self._current != "T2" or self._canary is None:
            return
        now = self._now()
        if now - self._last_canary < self._canary_interval_s:
            return
        self._last_canary = now
        try:
            succeeded = await self._canary()
        except Exception:
            succeeded = False
        self._canary_successes = self._canary_successes + 1 if succeeded else 0
        if self._canary_successes >= 2:
            self._transition("T1", "two successful canaries")

    def _next_available(self, desired: FetchTier) -> FetchTier:
        if desired in self._clients:
            return desired
        if desired == "T2" and "T3" in self._clients:
            return "T3"
        raise RuntimeError(f"No fallback client is configured after {self._current}")

    def _transition(self, tier: FetchTier, reason: str) -> None:
        if tier == self._current:
            return
        previous = self._current
        self._current = tier
        self.transitions.append(TierTransition(previous, tier, reason))
        self._reset_failures(previous)
        if tier == "T1":
            self._canary_successes = 0

    def _reset_failures(self, tier: FetchTier) -> None:
        if tier == "T1":
            self._auth_failures = 0
            self._rate_failures = 0
            self._waf_failures = 0
        elif tier == "T2":
            self._t2_failures = 0
