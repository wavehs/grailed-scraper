"""Small Algolia client used only during discovery probes."""

from __future__ import annotations

from typing import Any

from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.discovery.models import DiscoverySeed
from app.services.transport.protocols import HttpResponse, HttpTransport


class DiscoveryHttpError(RuntimeError):
    def __init__(self, status_code: int, operation: str) -> None:
        super().__init__(f"Discovery {operation} failed with HTTP {status_code}")
        self.status_code = status_code


class DiscoveryAlgoliaClient:
    def __init__(
        self,
        transport: HttpTransport,
        seed: DiscoverySeed,
        *,
        requests_per_minute: int | None = None,
    ) -> None:
        self._transport = transport
        self._seed = seed
        self._requests_per_minute = requests_per_minute or 60_000
        self._client = AlgoliaClient(
            transport,
            seed,
            requests_per_minute=self._requests_per_minute,
            max_concurrency=1,
            max_retries=0,
        )

    def set_rate_limit(self, requests_per_minute: int) -> None:
        self._requests_per_minute = max(requests_per_minute, 1)
        self._client = AlgoliaClient(
            self._transport,
            self._seed,
            requests_per_minute=self._requests_per_minute,
            max_concurrency=1,
            max_retries=0,
        )

    async def request(
        self, method: str, path: str, *, json_body: Any | None = None
    ) -> HttpResponse:
        return await self._client.raw_request(method, path, json_body=json_body)

    async def json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        allow_forbidden: bool = False,
    ) -> dict[str, Any] | None:
        response = await self.request(method, path, json_body=json_body)
        if allow_forbidden and response.status_code == 403:
            return None
        if response.status_code != 200:
            raise DiscoveryHttpError(response.status_code, _safe_operation(path))
        payload = response.json()
        if not isinstance(payload, dict):
            raise DiscoveryHttpError(502, path)
        return payload

    @staticmethod
    def index_path(index_name: str, suffix: str = "query") -> str:
        return AlgoliaClient.index_path(index_name, suffix)


def _safe_operation(path: str) -> str:
    if path.startswith("/1/keys/"):
        return "/1/keys/[redacted]"
    return path
