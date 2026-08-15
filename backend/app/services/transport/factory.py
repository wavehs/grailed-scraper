"""Select a transport while preserving a usable application on API drift."""

from __future__ import annotations

from app.core.config import Settings
from app.services.transport.capabilities import probe_capabilities
from app.services.transport.httpx_http import HttpxTransport
from app.services.transport.protocols import HttpTransport
from app.services.transport.proxy_manager import ProxyManager
from app.services.transport.scrapling_http import ScraplingHttpTransport


def create_proxy_manager(settings: Settings) -> ProxyManager:
    """Build the shared proxy policy from runtime configuration."""

    return ProxyManager(
        http_proxies=settings.proxy_pool("http") if settings.proxy_enabled else [],
        browser_proxies=settings.proxy_pool("browser") if settings.proxy_enabled else [],
        allow_direct_fallback=settings.proxy_allow_direct_fallback,
    )


def create_http_transport(settings: Settings, *, proxy: str | None = None) -> HttpTransport:
    """Prefer Scrapling T1, then fall back to httpx when it is unavailable."""

    configured_proxy = proxy
    if settings.proxy_enabled and configured_proxy is None:
        configured_proxy = create_proxy_manager(settings).select("default")
    if not settings.proxy_enabled:
        configured_proxy = None
    if probe_capabilities().t1_available:
        return ScraplingHttpTransport(
            proxy=configured_proxy, timeout_s=settings.parser_request_timeout_s
        )
    return HttpxTransport(proxy=configured_proxy, timeout_s=settings.parser_request_timeout_s)
