"""Settings-aware construction of the optional Tier 2 browser pool."""

from __future__ import annotations

from app.core.config import Settings
from app.services.sources.grailed.browser.session_pool import BrowserSessionPool
from app.services.transport.factory import create_proxy_manager


def create_browser_session_pool(
    settings: Settings, *, proxy: str | None = None
) -> BrowserSessionPool | None:
    """Return no pool when browser escalation is deliberately disabled."""

    if not settings.fetch_tier_allow_browser:
        return None
    if settings.proxy_enabled and proxy is None:
        proxy = create_proxy_manager(settings).select("browser-default", pool="browser")
    if not settings.proxy_enabled:
        proxy = None
    return BrowserSessionPool(
        max_pages=settings.browser_max_pages,
        restart_every_requests=settings.browser_restart_every_requests,
        restart_every_s=settings.browser_restart_every_minutes * 60,
        proxy=proxy,
        use_raw_fallback=settings.browser_use_raw_fallback,
    )
