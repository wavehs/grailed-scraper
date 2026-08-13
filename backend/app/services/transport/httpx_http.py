"""httpx fallback implementation of :class:`HttpTransport`."""

from __future__ import annotations

from typing import Any

import httpx

from app.services.transport.protocols import HttpResponse


class HttpxTransport:
    """Cookie-preserving asynchronous HTTP transport suitable for tests and fallback."""

    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(proxy=proxy, timeout=timeout_s, transport=transport)

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
