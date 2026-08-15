"""Production read-only Algolia client over the repository transport protocol."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol
from urllib.parse import quote

from app.services.parser.observability import RunMetrics
from app.services.sources.grailed.algolia.exceptions import (
    AlgoliaAuthError,
    AlgoliaBadQuery,
    AlgoliaError,
    AlgoliaIndexNotFound,
    AlgoliaRateLimited,
    AlgoliaTransient,
    RequestBudgetExceeded,
    WafChallenge,
)
from app.services.sources.grailed.algolia.hosts import AlgoliaHostPool, algolia_hosts
from app.services.sources.grailed.algolia.models import (
    AlgoliaPage,
    AlgoliaQuery,
    AlgoliaRequest,
    FacetValue,
    _integer,
)
from app.services.sources.grailed.algolia.query_builder import build_params
from app.services.transport.circuit_breaker import CircuitBreaker
from app.services.transport.protocols import HttpResponse, HttpTransport
from app.services.transport.proxy_manager import ProxyManager
from app.services.transport.rate_limiter import RateLimiter
from app.services.transport.resilience import retry_after_seconds
from app.services.transport.response_cache import ResponseCache

Sleep = Callable[[float], Awaitable[None]]


class AlgoliaCredentials(Protocol):
    @property
    def app_id(self) -> str: ...

    @property
    def api_key(self) -> str: ...

    @property
    def algolia_agent(self) -> str | None: ...

    @property
    def session_headers(self) -> tuple[tuple[str, str], ...]: ...


CredentialRefresh = Callable[[], Awaitable[AlgoliaCredentials | None]]


class AlgoliaClient:
    """Expose only Algolia's public search endpoints and never leak credentials."""

    def __init__(
        self,
        transport: HttpTransport,
        seed: AlgoliaCredentials,
        *,
        requests_per_minute: int = 90,
        max_concurrency: int = 3,
        max_retries: int = 3,
        max_requests: int | None = None,
        multiquery_batch_size: int = 8,
        timeout_s: float = 15.0,
        rate_limiter: RateLimiter | None = None,
        sleep: Sleep = asyncio.sleep,
        hosts: Sequence[str] | None = None,
        tier: str | None = None,
        metrics: RunMetrics | None = None,
        response_cache: ResponseCache | None = None,
        proxy_key: str = "direct",
        proxy_manager: ProxyManager | None = None,
        proxy_url: str | None = None,
        refresh_credentials: CredentialRefresh | None = None,
    ) -> None:
        if not 1 <= multiquery_batch_size <= 8:
            raise ValueError("multiquery_batch_size must be between 1 and 8")
        if max_requests is not None and max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        self._transport = transport
        self._seed = seed
        self._custom_hosts = hosts is not None
        candidates = tuple(hosts) if hosts is not None else algolia_hosts(seed.app_id)
        self._hosts = AlgoliaHostPool(candidates)
        self._max_retries = max_retries
        self._max_requests = max_requests
        self._requests_started = (
            sum(metrics.requests_by_tier.values()) if metrics is not None else 0
        )
        self._batch_size = multiquery_batch_size
        self._timeout_s = timeout_s
        self._limiter = rate_limiter or RateLimiter(
            requests_per_minute=requests_per_minute,
            max_concurrent_per_host=max_concurrency,
        )
        self._sleep = sleep
        self._tier = tier or "T1"
        self._metrics = metrics
        self._cache = response_cache or ResponseCache()
        self._proxy_key = proxy_key
        self._proxy_manager = proxy_manager
        self._proxy_url = proxy_url
        self._refresh_credentials = refresh_credentials
        self._credential_refresh_lock = asyncio.Lock()
        self._breakers: dict[tuple[str, str, str], CircuitBreaker] = {}

    def circuit_statuses(self) -> list[dict[str, str]]:
        return [
            {"tier": tier, "host": host, "proxy": proxy, "state": breaker.state.value}
            for (tier, host, proxy), breaker in self._breakers.items()
        ]

    async def search(self, index_name: str, query: AlgoliaQuery) -> AlgoliaPage:
        payload = await self._json(
            "POST",
            self.index_path(index_name),
            json_body={"params": build_params(query)},
            operation="search",
        )
        return AlgoliaPage.from_payload(payload)

    async def multi_query(self, requests: Sequence[AlgoliaRequest]) -> tuple[AlgoliaPage, ...]:
        pages: list[AlgoliaPage] = []
        for offset in range(0, len(requests), self._batch_size):
            batch = requests[offset : offset + self._batch_size]
            payload = await self._json(
                "POST",
                "/1/indexes/*/queries",
                json_body={
                    "requests": [
                        {"indexName": request.index_name, "params": build_params(request.query)}
                        for request in batch
                    ]
                },
                operation="multi-query",
            )
            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list) or len(raw_results) != len(batch):
                raise AlgoliaTransient("multi-query response")
            pages.extend(
                AlgoliaPage.from_payload(item) for item in raw_results if isinstance(item, dict)
            )
            if len(pages) < offset + len(batch):
                raise AlgoliaTransient("multi-query response")
        return tuple(pages)

    async def browse(
        self, index_name: str, query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage:
        body: dict[str, Any] = {"params": build_params(query, include_cursor=True)}
        if cursor is not None:
            body["cursor"] = cursor
        payload = await self._json(
            "POST",
            self.index_path(index_name, "browse"),
            json_body=body,
            operation="browse",
        )
        return AlgoliaPage.from_payload(payload)

    async def search_facet_values(
        self,
        index_name: str,
        facet_name: str,
        facet_query: str,
        *,
        query: AlgoliaQuery | None = None,
    ) -> tuple[FacetValue, ...]:
        body: dict[str, Any] = {"facetQuery": facet_query}
        if query is not None:
            body["params"] = build_params(query)
        payload = await self._json(
            "POST",
            self.index_path(index_name, f"facets/{quote(facet_name, safe='')}/query"),
            json_body=body,
            operation="facet values",
        )
        raw_hits = payload.get("facetHits", [])
        if not isinstance(raw_hits, list):
            return ()
        return tuple(
            FacetValue(
                value=str(item["value"]),
                count=_integer(item.get("count"), 0),
                highlighted=str(item["highlighted"])
                if item.get("highlighted") is not None
                else None,
            )
            for item in raw_hits
            if isinstance(item, dict) and "value" in item
        )

    async def raw_request(
        self, method: str, path: str, *, json_body: Any | None = None
    ) -> HttpResponse:
        """Compatibility seam for discovery probes that inspect status codes."""

        return await self._request(method, path, json_body=json_body, operation="discovery")

    async def _json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None,
        operation: str,
    ) -> dict[str, Any]:
        response = await self._request(method, path, json_body=json_body, operation=operation)
        if response.status_code in {401, 403} and self._refresh_credentials is not None:
            observed_seed = self._seed
            async with self._credential_refresh_lock:
                if self._seed is observed_seed:
                    refreshed = await self._refresh_credentials()
                    if refreshed is not None:
                        self._seed = refreshed
                        if not self._custom_hosts and refreshed.app_id != observed_seed.app_id:
                            self._hosts = AlgoliaHostPool(algolia_hosts(refreshed.app_id))
            if self._seed is not observed_seed:
                response = await self._request(
                    method, path, json_body=json_body, operation=operation
                )
        raise_for_status(response.status_code, operation)
        try:
            payload = response.json()
        except (UnicodeDecodeError, ValueError) as exc:
            raise WafChallenge(operation, response.status_code) from exc
        if not isinstance(payload, dict):
            raise WafChallenge(operation, response.status_code)
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None,
        operation: str,
    ) -> HttpResponse:
        headers = dict(self._seed.session_headers)
        headers.update(
            {
                "x-algolia-application-id": self._seed.app_id,
                "x-algolia-api-key": self._seed.api_key,
                "Content-Type": "application/json",
                "Origin": "https://www.grailed.com",
                "Referer": "https://www.grailed.com/",
            }
        )
        agent_params = (
            {"x-algolia-agent": self._seed.algolia_agent} if self._seed.algolia_agent else None
        )
        last_response: HttpResponse | None = None
        last_error: Exception | None = None
        candidates = self._hosts.candidates()
        for attempt in range(self._max_retries + 1):
            host = candidates[attempt % len(candidates)]
            url = f"{host}{path}"
            cache_key = self._cache.key(method, url, agent_params, json_body)
            cached = self._cache.get(cache_key)
            if self._metrics is not None:
                self._metrics.record_cache(cached is not None)
            if cached is not None:
                return cached
            if self._max_requests is not None and self._requests_started >= self._max_requests:
                raise RequestBudgetExceeded(
                    f"Parser request budget exhausted at {self._max_requests} requests"
                )
            self._requests_started += 1
            breaker = self._breakers.setdefault(
                (self._tier, host, self._proxy_key), CircuitBreaker()
            )
            breaker.allow_request()
            started = time.perf_counter()
            try:
                async with self._limiter.limit(url):
                    response = await self._transport.request(
                        method,
                        url,
                        headers=headers,
                        params=agent_params,
                        json_body=json_body,
                        timeout_s=self._timeout_s,
                    )
            except Exception as exc:
                breaker.record_failure()
                last_error = exc
                self._hosts.mark_down(host)
                if self._proxy_manager is not None and self._proxy_url is not None:
                    self._proxy_manager.record_failure(self._proxy_url)
                if self._metrics is not None and attempt < self._max_retries:
                    self._metrics.retries += 1
                if attempt < self._max_retries:
                    await self._sleep(min(2**attempt, 30.0))
                continue
            duration_ms = (time.perf_counter() - started) * 1000
            if self._metrics is not None:
                self._metrics.record_response(self._tier, response.status_code, duration_ms)
            last_response = response
            if response.status_code == 429 and attempt < self._max_retries:
                breaker.record_failure()
                if self._metrics is not None:
                    self._metrics.retries += 1
                await self._sleep(retry_after_seconds(response.headers) or (5 * 3**attempt))
                continue
            if response.status_code >= 500 and attempt < self._max_retries:
                breaker.record_failure()
                if self._metrics is not None:
                    self._metrics.retries += 1
                self._hosts.mark_down(host)
                await self._sleep(min(2**attempt, 30.0))
                continue
            if response.status_code < 400:
                breaker.record_success()
                self._cache.set(cache_key, response)
                if self._proxy_manager is not None and self._proxy_url is not None:
                    self._proxy_manager.record_success(self._proxy_url)
            else:
                breaker.record_failure()
                if self._proxy_manager is not None and self._proxy_url is not None:
                    self._proxy_manager.record_failure(self._proxy_url)
            return response
        if last_response is not None:
            return last_response
        raise AlgoliaTransient(operation) from last_error

    @staticmethod
    def index_path(index_name: str, suffix: str = "query") -> str:
        return f"/1/indexes/{quote(index_name, safe='')}/{suffix}"


def raise_for_status(status_code: int, operation: str) -> None:
    if status_code == 200:
        return
    error_type: type[AlgoliaError]
    if status_code == 400:
        error_type = AlgoliaBadQuery
    elif status_code in {401, 403}:
        error_type = AlgoliaAuthError
    elif status_code == 404:
        error_type = AlgoliaIndexNotFound
    elif status_code == 429:
        error_type = AlgoliaRateLimited
    elif status_code >= 500:
        error_type = AlgoliaTransient
    else:
        error_type = AlgoliaError
    raise error_type(operation, status_code)
