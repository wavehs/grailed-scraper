"""Stable interfaces shielding source code from HTTP implementation details."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Normalized response returned by every HTTP engine."""

    status_code: int
    headers: dict[str, str]
    content: bytes
    url: str

    def json(self) -> Any:
        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


@runtime_checkable
class HttpTransport(Protocol):
    """Minimal async HTTP API used by source adapters."""

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
    ) -> HttpResponse: ...

    async def close(self) -> None: ...


@runtime_checkable
class BrowserPage(Protocol):
    async def evaluate(self, script: str, arg: Any | None = None) -> Any: ...

    def on(self, event: str, callback: Callable[[Any], Any]) -> None: ...

    async def goto(
        self, url: str, *, wait_until: str | None = None, timeout: float | None = None
    ) -> Any: ...

    async def wait_for_timeout(self, timeout: float) -> None: ...


@runtime_checkable
class BrowserSession(Protocol):
    async def acquire_page(self) -> BrowserPage: ...

    async def release_page(self, page: BrowserPage) -> None: ...

    async def close(self) -> None: ...
