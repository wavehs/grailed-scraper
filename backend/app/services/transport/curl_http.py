"""curl_cffi adapter with a Chrome TLS fingerprint, kept isolated from source code."""

from __future__ import annotations

from typing import Any

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from app.services.transport.protocols import HttpResponse


class TransportError(RuntimeError):
    """Network-level failure; the message never contains request URLs or headers."""


class CurlHttpTransport:
    """One session per transport so cookies and fingerprint stay consistent."""

    def __init__(self, *, timeout_s: float = 15.0, impersonate: str = "chrome") -> None:
        self._timeout_s = timeout_s
        self._impersonate = impersonate
        self._session: AsyncSession[Any] | None = None

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
        if self._session is None:
            self._session = AsyncSession(impersonate=self._impersonate)
        try:
            response = await self._session.request(
                method.upper(),  # type: ignore[arg-type]
                url,
                headers=headers,
                params=params,
                json=json_body,
                data=content,
                timeout=timeout_s or self._timeout_s,
                allow_redirects=True,
            )
        except RequestException as exc:
            raise TransportError(type(exc).__name__) from None
        return HttpResponse(
            status_code=int(response.status_code),
            headers={str(key): str(value) for key, value in response.headers.items()},
            content=bytes(response.content),
            url=str(response.url),
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
