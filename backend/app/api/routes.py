"""Routes available during the backend foundation stage."""

from __future__ import annotations

from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.ai_grouping import router as ai_grouping_router
from app.api.analytics import router as analytics_router
from app.api.brands import router as brands_router
from app.api.discovery import router as discovery_router
from app.api.errors import ApiError
from app.api.parser import router as parser_router
from app.api.settings import get_effective_settings
from app.api.settings import router as settings_router
from app.core.config import Settings
from app.core.runtime import resolve_revision
from app.db.session import get_db
from app.services.transport.factory import create_proxy_manager

router = APIRouter(prefix="/api")
router.include_router(discovery_router)
router.include_router(brands_router)
router.include_router(parser_router)
router.include_router(analytics_router)
router.include_router(settings_router)
router.include_router(ai_grouping_router)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    source_mode: Literal["live"]
    request_id: str
    version: str
    revision: str
    environment: Literal["development", "test", "production"]


class ProxyTestStatus(BaseModel):
    proxy: str
    success_rate: float
    successes: int
    failures: int
    consecutive_failures: int
    cooling_down: bool
    cooldown_remaining_s: float | None


class ProxyTestResponse(BaseModel):
    enabled: bool
    direct_fallback_allowed: bool
    proxies: list[ProxyTestStatus]


async def _probe_proxy(proxy_url: str) -> bool:
    """Perform a small, credential-free egress probe through one proxy."""

    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client:
        response = await client.get("https://api.ipify.org?format=json")
    return response.is_success


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> HealthResponse:
    """Confirm the API can serve traffic and reach its configured database."""

    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise ApiError(503, "database_unavailable", "Database is unavailable") from exc

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        source_mode=settings.source_mode,
        request_id=request.state.request_id,
        version=__version__,
        revision=resolve_revision(settings.revision),
        environment=settings.environment,
    )


@router.post("/settings/proxies/test", response_model=ProxyTestResponse, tags=["settings"])
async def test_proxies(
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> ProxyTestResponse:
    """Probe configured egress proxies without returning their credentials."""

    manager = create_proxy_manager(settings)
    statuses = await manager.test_all(_probe_proxy)
    return ProxyTestResponse(
        enabled=settings.proxy_enabled,
        direct_fallback_allowed=settings.proxy_allow_direct_fallback,
        proxies=[ProxyTestStatus.model_validate(item) for item in statuses],
    )
