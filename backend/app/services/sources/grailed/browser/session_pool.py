"""Reusable Scrapling AsyncStealthySession page pool for Tier 2."""

from __future__ import annotations

import asyncio
import importlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from app.services.transport.protocols import BrowserPage

SessionFactory = Callable[[], Awaitable[Any]]


class BrowserSessionPool:
    """Maintain one browser per run and recycle it before it becomes unhealthy."""

    def __init__(
        self,
        *,
        max_pages: int = 2,
        restart_every_requests: int = 300,
        restart_every_s: float = 20 * 60,
        proxy: str | None = None,
        session_factory: SessionFactory | None = None,
        use_raw_fallback: bool = False,
    ) -> None:
        self._max_pages = max_pages
        self._restart_every_requests = restart_every_requests
        self._restart_every_s = restart_every_s
        self._proxy = proxy
        self._factory = session_factory or self._create_scrapling_session
        self._use_raw_fallback = use_raw_fallback
        self._session: Any | None = None
        self._pages: asyncio.Queue[BrowserPage] = asyncio.Queue(max_pages)
        self._created_pages = 0
        self._requests = 0
        self._started_at = 0.0
        self._lock = asyncio.Lock()
        self.restart_count = 0

    async def _create_scrapling_session(self) -> Any:
        fetchers = importlib.import_module("scrapling.fetchers")
        session_type = fetchers.AsyncStealthySession
        session = session_type(proxy=self._proxy) if self._proxy else session_type()
        await session.start()
        return session

    async def _create_session(self) -> Any:
        try:
            return await self._factory()
        except Exception:
            if not self._use_raw_fallback:
                raise
            from app.services.sources.grailed.browser.raw_camoufox import create_raw_camoufox

            return await create_raw_camoufox()

    @staticmethod
    async def _new_page(session: Any) -> BrowserPage:
        owner = session if hasattr(session, "new_page") else getattr(session, "context", None)
        if owner is None:
            raise RuntimeError("StealthySession did not expose a browser context")
        return cast(BrowserPage, await owner.new_page())

    async def acquire_page(self) -> BrowserPage:
        await self._restart_if_due()
        try:
            return self._pages.get_nowait()
        except asyncio.QueueEmpty:
            async with self._lock:
                if self._session is None:
                    self._session = await self._create_session()
                    self._started_at = time.monotonic()
                if self._created_pages < self._max_pages:
                    page = await self._new_page(self._session)
                    self._created_pages += 1
                    return page
            return await self._pages.get()

    async def release_page(self, page: BrowserPage) -> None:
        self._pages.put_nowait(page)

    @asynccontextmanager
    async def page(self) -> AsyncIterator[BrowserPage]:
        page = await self.acquire_page()
        try:
            yield page
        finally:
            await self.release_page(page)

    async def record_request(self) -> None:
        self._requests += 1
        await self._restart_if_due()

    async def _restart_if_due(self) -> None:
        due_by_count = self._requests >= self._restart_every_requests
        due_by_age = (
            self._session is not None
            and time.monotonic() - self._started_at >= self._restart_every_s
        )
        if due_by_count or due_by_age:
            await self.close()
            self._requests = 0
            self.restart_count += 1

    async def close(self) -> None:
        async with self._lock:
            while not self._pages.empty():
                page = self._pages.get_nowait()
                close = getattr(page, "close", None)
                if close is not None:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
            session, self._session = self._session, None
            self._created_pages = 0
            if session is None:
                return
            exit_session = getattr(session, "__aexit__", None)
            if exit_session is not None:
                await exit_session(None, None, None)
                return
            close = getattr(session, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
