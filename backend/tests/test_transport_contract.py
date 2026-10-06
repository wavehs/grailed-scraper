"""Contract of the curl_cffi transport adapter, exercised through a fake session."""

from __future__ import annotations

from typing import Any

import pytest
from curl_cffi.requests.exceptions import RequestException

from app.services.transport import curl_http
from app.services.transport.curl_http import CurlHttpTransport, TransportError


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes, url: str) -> None:
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = {"content-type": "application/json"}


class _FakeSession:
    instances: list[_FakeSession] = []

    def __init__(self, **kwargs: Any) -> None:
        self.options = kwargs
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        _FakeSession.instances.append(self)

    async def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/fail"):
            raise RequestException("https://secret.test/?x-algolia-api-key=leak")
        return _FakeResponse(200, b'{"ok": true}', url)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSession.instances.clear()
    monkeypatch.setattr(curl_http, "AsyncSession", _FakeSession)


async def test_one_session_is_reused_and_closed() -> None:
    transport = CurlHttpTransport(timeout_s=7)
    first = await transport.request(
        "post", "https://algolia.test/first", json_body={"q": "тест"}, headers={"x": "1"}
    )
    second = await transport.request("GET", "https://algolia.test/second", params={"a": "b c"})
    await transport.close()

    assert first.status_code == 200
    assert first.json() == {"ok": True}
    assert second.url == "https://algolia.test/second"
    [session] = _FakeSession.instances
    assert session.options == {"impersonate": "chrome"}
    assert session.closed
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["json"] == {"q": "тест"}
    assert session.calls[0]["timeout"] == 7
    assert session.calls[1]["params"] == {"a": "b c"}


async def test_network_errors_do_not_leak_request_details() -> None:
    transport = CurlHttpTransport()
    with pytest.raises(TransportError) as raised:
        await transport.request("GET", "https://algolia.test/fail")
    await transport.close()

    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None
