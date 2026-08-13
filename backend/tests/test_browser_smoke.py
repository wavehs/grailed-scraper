"""Opt-in smoke test for the real Scrapling-managed Camoufox engine."""

from __future__ import annotations

from typing import Any, cast

import pytest

from app.services.sources.grailed.browser.session_pool import BrowserSessionPool


@pytest.mark.browser
async def test_scrapling_camoufox_opens_and_closes_example_page() -> None:
    pool = BrowserSessionPool(max_pages=1)
    page: Any | None = None
    try:
        page = cast(Any, await pool.acquire_page())
        await page.goto("https://example.com", wait_until="domcontentloaded", timeout=30_000)
        assert "Example Domain" in await page.title()
    finally:
        if page is not None:
            await pool.release_page(page)
        await pool.close()
