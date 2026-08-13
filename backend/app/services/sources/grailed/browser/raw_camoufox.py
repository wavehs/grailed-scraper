"""Emergency Camoufox adapter; never used unless explicitly enabled by settings."""

from __future__ import annotations

from typing import Any


class RawCamoufoxSession:
    """Small session façade used only when Scrapling's browser API is unavailable."""

    def __init__(self) -> None:
        self._engine: Any | None = None
        self._context: Any | None = None

    async def start(self) -> None:
        from camoufox.async_api import AsyncCamoufox  # type: ignore[import-not-found]

        self._engine = AsyncCamoufox()
        browser = await self._engine.__aenter__()
        self._context = await browser.new_context()

    async def new_page(self) -> Any:
        if self._context is None:
            raise RuntimeError("Raw Camoufox session has not started")
        return await self._context.new_page()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._engine is not None:
            await self._engine.__aexit__(None, None, None)
            self._engine = None


async def create_raw_camoufox() -> RawCamoufoxSession:
    """Create the emergency browser directly after Scrapling compatibility failure."""

    session = RawCamoufoxSession()
    await session.start()
    return session
