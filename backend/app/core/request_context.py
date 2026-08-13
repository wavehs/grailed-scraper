"""Request-scoped identifiers shared by handlers and structured logs."""

from __future__ import annotations

import re
import time
from uuid import uuid4

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a safe request identifier to each response and log event."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        candidate = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else str(uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            structlog.get_logger(__name__).info(
                "request_complete",
                method=request.method,
                path=request.url.path,
                status_code=getattr(locals().get("response"), "status_code", 500),
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
