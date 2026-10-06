"""Construct the single supported HTTP transport."""

from __future__ import annotations

from app.core.config import Settings
from app.services.transport.curl_http import CurlHttpTransport
from app.services.transport.protocols import HttpTransport


def create_http_transport(settings: Settings) -> HttpTransport:
    return CurlHttpTransport(timeout_s=settings.parser_request_timeout_s)
