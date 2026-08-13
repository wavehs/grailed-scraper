"""Network-denying T0 transport backed by the in-process fake Algolia ASGI app."""

from __future__ import annotations

from typing import Any

import httpx

from app.services.parser.mock.fake_algolia_server import (
    FakeAlgoliaScenario,
    create_fake_algolia_app,
)
from app.services.parser.mock.generator import MockCatalog
from app.services.transport.protocols import HttpResponse

MOCK_ALGOLIA_BASE_URL = "http://mock-algolia.local"


class MockHttpTransport:
    """Serve only the fixture host; all other targets fail before a socket is opened."""

    def __init__(
        self, *, catalog: MockCatalog | None = None, scenario: FakeAlgoliaScenario | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_fake_algolia_app(catalog, scenario)),
            base_url=MOCK_ALGOLIA_BASE_URL,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json_body: Any | None = None,
        content: bytes | None = None,
        timeout_s: float | None = None,
    ) -> HttpResponse:
        target = httpx.URL(url)
        expected = httpx.URL(MOCK_ALGOLIA_BASE_URL)
        if target.scheme != expected.scheme or target.host != expected.host:
            raise ValueError(f"T0 mock transport rejects external URL: {url}")
        response = await self._client.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            content=content,
            timeout=timeout_s,
        )
        return HttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            url=str(response.url),
        )

    async def close(self) -> None:
        await self._client.aclose()
