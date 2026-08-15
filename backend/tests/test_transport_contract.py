"""Contract shared by the two HTTP transport implementations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from app.services.transport.httpx_http import HttpxTransport
from app.services.transport.protocols import HttpTransport
from app.services.transport.scrapling_http import ScraplingHttpTransport, _SecretFilter


@dataclass
class _FakeResponse:
    status: int
    headers: dict[str, str]
    content: bytes | None
    url: str
    body: bytes | None = None


class _FakeSession:
    def __init__(self, **_: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.cookie = ""

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def _request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        headers = dict(kwargs.get("headers") or {})
        if self.cookie:
            headers.setdefault("cookie", self.cookie)
        self.calls.append({"method": method, "url": url, "headers": headers, **kwargs})
        if url.endswith("/first"):
            self.cookie = "session=kept"
            return _FakeResponse(
                200, {"set-cookie": self.cookie}, None, url, body=b'{"ok":true}'
            )
        return _FakeResponse(201, {"content-type": "text/plain"}, b"plain", url)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._request("POST", url, **kwargs)


@dataclass(frozen=True)
class _Adapter:
    name: str
    create: Callable[[], HttpTransport]


def _httpx_transport() -> HttpTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/first":
            return httpx.Response(200, headers={"set-cookie": "session=kept"}, json={"ok": True})
        assert request.headers["cookie"] == "session=kept"
        return httpx.Response(201, content=b"plain", headers={"content-type": "text/plain"})

    return HttpxTransport(transport=httpx.MockTransport(handler))


def _scrapling_transport() -> HttpTransport:
    return ScraplingHttpTransport(session_factory=_FakeSession)


def test_scrapling_log_filter_masks_algolia_keys() -> None:
    record = logging.LogRecord(
        "scrapling",
        logging.INFO,
        "",
        0,
        "GET /1/keys/secret-value?x-algolia-api-key=another-secret",
        (),
        None,
    )

    assert _SecretFilter().filter(record)
    assert record.getMessage() == (
        "GET /1/keys/[redacted]?x-algolia-api-key=[redacted]"
    )


@pytest.mark.parametrize(
    "adapter", [_Adapter("httpx", _httpx_transport), _Adapter("scrapling", _scrapling_transport)]
)
async def test_http_transport_contract(adapter: _Adapter) -> None:
    transport = adapter.create()
    try:
        first = await transport.request(
            "POST",
            "https://algolia.test/first",
            headers={"x-client": "contract"},
            json_body={"q": "тест"},
        )
        second = await transport.request(
            "GET", "https://algolia.test/second", params={"quoted": "a b"}
        )
    finally:
        await transport.close()

    assert first.status_code == 200
    assert first.json() == {"ok": True}
    assert second.status_code == 201
    assert second.text == "plain"
