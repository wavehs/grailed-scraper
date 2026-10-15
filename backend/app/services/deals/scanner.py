"""Run the deal scan every few minutes while ``deals_enabled`` is on (v2 plan 4.1, D12).

The scanner shares the market lock with collections: SQLite has one writer, so a cycle that
finds a collection running is skipped rather than queued. A scan started by hand runs even
when the scanner is off. What it last did is kept in memory for the Deals page.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.deals.scan import ScanReport, run_scan
from app.services.parser.credentials import as_algolia, credential_refresher, stored_credential
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.transport.factory import create_http_transport

# Long enough that a short-lived app (tests, a quick restart) never reaches the network.
STARTUP_DELAY_S = 90.0
# How often a disabled scanner looks whether it was switched on.
IDLE_CHECK_S = 300.0

SettingsLoader = Callable[[AsyncSession, Settings], Awaitable[Settings]]
Scan = Callable[[async_sessionmaker[AsyncSession], Settings, asyncio.Lock], Awaitable[ScanReport]]


async def network_scan(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, grouping_lock: asyncio.Lock
) -> ScanReport:
    async with sessions() as session:
        credential = await stored_credential(session)
    if credential is None or not credential.active_index:
        raise RuntimeError("discovery_required")
    transport = create_http_transport(settings)
    try:
        client = AlgoliaClient(
            transport,
            as_algolia(credential),
            requests_per_minute=settings.deals_rpm,
            max_concurrency=1,
            max_retries=settings.parser_max_retries,
            timeout_s=settings.parser_request_timeout_s,
            tier="T1",
            refresh_credentials=credential_refresher(sessions, settings, transport),
        )
        return await run_scan(
            sessions, settings, client.search, credential, grouping_lock=grouping_lock
        )
    finally:
        await transport.close()


class DealScanner:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        base: Settings,
        *,
        load_settings: SettingsLoader,
        market_lock: asyncio.Lock,
        grouping_lock: asyncio.Lock,
        scan: Scan = network_scan,
        startup_delay_s: float = STARTUP_DELAY_S,
    ) -> None:
        self._sessions = sessions
        self._base = base
        self._load_settings = load_settings
        self._market_lock = market_lock
        self._grouping_lock = grouping_lock
        self._scan = scan
        self._startup_delay_s = startup_delay_s
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._manual: asyncio.Task[ScanReport | None] | None = None
        self._running = False
        self._last: ScanReport | None = None
        self._last_error: str | None = None
        self._skipped_at: datetime | None = None

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "last": self._last.as_dict() if self._last else None,
            "last_error": self._last_error,
            "skipped_at": self._skipped_at.isoformat() if self._skipped_at else None,
        }

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run_forever(), name="deal-scanner")

    async def close(self) -> None:
        tasks = [task for task in (self._task, self._manual) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = self._manual = None

    def trigger(self) -> bool:
        """Start a scan now in the background; False when one is running or would wait."""

        if self._running or self._market_lock.locked():
            return False
        if self._manual is not None and not self._manual.done():
            return False
        self._manual = asyncio.create_task(self.tick(force=True), name="deal-scan")
        return True

    async def run_forever(self) -> None:
        await self._sleep(self._startup_delay_s)
        while True:
            interval = IDLE_CHECK_S
            try:
                settings = await self._settings()
                if settings.deals_enabled:
                    interval = settings.deals_interval_minutes * 60.0
                    await self.tick()
            except Exception:
                structlog.get_logger(__name__).exception("deal_scan_tick_failed")
            await self._sleep(interval)

    async def tick(self, *, force: bool = False) -> ScanReport | None:
        settings = await self._settings()
        if not (settings.deals_enabled or force):
            return None
        if self._running or self._market_lock.locked():
            self._skipped_at = datetime.now().astimezone()
            structlog.get_logger(__name__).info("deal_scan_skipped_market_busy")
            return None
        async with self._market_lock:
            self._running = True
            try:
                report = await self._scan(self._sessions, settings, self._grouping_lock)
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"[:500]
                structlog.get_logger(__name__).warning(
                    "deal_scan_failed", error_type=type(exc).__name__
                )
                return None
            finally:
                self._running = False
        self._last, self._last_error = report, None
        structlog.get_logger(__name__).info("deal_scan_done", **_log_fields(report))
        return report

    async def _settings(self) -> Settings:
        async with self._sessions() as session:
            return await self._load_settings(session, self._base)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except TimeoutError:
            pass
        self._wake.clear()


def _log_fields(report: ScanReport) -> dict[str, Any]:
    return {
        "brands": report.brands,
        "hits": report.hits,
        "stored": report.stored,
        "failed": len(report.failed),
        "deals": report.deals.deals if report.deals else 0,
        "new": report.deals.new if report.deals else 0,
    }
