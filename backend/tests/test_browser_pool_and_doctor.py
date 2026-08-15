"""Browser pool lifecycle and capability CLI checks do not need a browser."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, cast

import pytest

from app.core.config import Settings
from app.services.parser.observability import RunMetrics
from app.services.parser.runtime import _Resources
from app.services.sources.grailed.browser.factory import create_browser_session_pool
from app.services.sources.grailed.browser.session_pool import BrowserSessionPool
from app.services.transport.capabilities import CapabilityReport


class _Page:
    def __init__(self) -> None:
        self.closed = False

    async def evaluate(self, script: str, arg: Any | None = None) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self) -> None:
        self.pages: list[_Page] = []
        self.closed = False

    async def new_page(self) -> _Page:
        page = _Page()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True


class _BrokenBrowser:
    async def close(self) -> None:
        raise RuntimeError("browser close failed")


class _ClosingTransport:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_browser_pool_reuses_page_and_closes_everything() -> None:
    session = _Session()

    async def factory() -> _Session:
        return session

    pool = BrowserSessionPool(max_pages=1, session_factory=factory)
    first = await pool.acquire_page()
    await pool.release_page(first)
    assert await pool.acquire_page() is first
    await pool.release_page(first)
    await pool.close()
    assert session.closed
    assert session.pages[0].closed


@pytest.mark.asyncio
async def test_run_resources_close_transport_when_browser_close_fails() -> None:
    transport = _ClosingTransport()
    resources = _Resources(
        fetcher=cast(Any, None),
        transport=cast(Any, transport),
        browser=cast(Any, _BrokenBrowser()),
        metrics=RunMetrics(),
        algolia=cast(Any, None),
    )

    with pytest.raises(RuntimeError, match="browser close failed"):
        await resources.close()

    assert transport.closed


def test_capability_report_is_safe_json() -> None:
    report = CapabilityReport("0.4.11", None, True, True)
    assert json.loads(json.dumps(report.as_dict()))["t2_available"] is True


def test_browser_pool_can_be_disabled_without_initializing_a_browser() -> None:
    assert create_browser_session_pool(Settings(fetch_tier_allow_browser=False)) is None


def test_doctor_command_prints_json() -> None:
    output = subprocess.run(
        [sys.executable, "-m", "app.cli", "doctor"], check=True, capture_output=True, text=True
    )
    report = json.loads(output.stdout)
    assert {"t1_available", "t2_available", "scrapling_version"} <= report.keys()
