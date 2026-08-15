"""Scrapling FetcherSession adapter, kept isolated from the rest of the app."""

from __future__ import annotations

import importlib
import inspect
import logging
import re
from collections.abc import Callable
from typing import Any

from app.services.transport.protocols import HttpResponse

_KEY_PATH = re.compile(r"(?i)(/1/keys/)[^?\s>]+")
_KEY_PARAM = re.compile(r"(?i)(x-algolia-api-key(?:=|%3D))[^&\s>]+")


class _SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _KEY_PARAM.sub(
            r"\1[redacted]", _KEY_PATH.sub(r"\1[redacted]", record.getMessage())
        )
        record.args = ()
        return True


class ScraplingHttpTransport:
    """Use one FetcherSession so cookies and fingerprint stay consistent."""

    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout_s: float = 15.0,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        if session_factory is None:
            fetchers = importlib.import_module("scrapling.fetchers")
            session_factory = fetchers.FetcherSession
        for handler in logging.getLogger("scrapling").handlers:
            if not any(isinstance(item, _SecretFilter) for item in handler.filters):
                handler.addFilter(_SecretFilter())
        self._manager = session_factory(proxy=proxy, timeout=timeout_s)
        self._session: Any | None = None
        self._timeout_s = timeout_s

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
        session = await self._ensure_session()
        request = getattr(session, method.lower(), None)
        if request is None:
            raise ValueError(f"Unsupported HTTP method for Scrapling: {method}")
        response = request(
            url,
            headers=headers,
            params=params,
            json=json_body,
            data=content,
            timeout=timeout_s or self._timeout_s,
        )
        if inspect.isawaitable(response):
            response = await response
        response_content = getattr(response, "body", None)
        if response_content is None:
            response_content = getattr(response, "content", None)
        if response_content is None:
            response_content = str(getattr(response, "text", "")).encode()
        return HttpResponse(
            status_code=int(getattr(response, "status", getattr(response, "status_code", 0))),
            headers={str(k): str(v) for k, v in dict(getattr(response, "headers", {})).items()},
            content=bytes(response_content),
            url=str(getattr(response, "url", url)),
        )

    async def _ensure_session(self) -> Any:
        if self._session is None:
            enter = getattr(self._manager, "__aenter__", None)
            self._session = await enter() if enter is not None else self._manager
        return self._session

    async def close(self) -> None:
        if self._session is None:
            return
        exit_session = getattr(self._manager, "__aexit__", None)
        if exit_session is not None:
            await exit_session(None, None, None)
        else:
            close = getattr(self._session, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
        self._session = None
