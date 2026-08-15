"""Public source-discovery endpoints with secret-free response models."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.settings import get_effective_settings
from app.core.config import Settings
from app.core.privacy import require_live_compliance
from app.db.session import get_db
from app.services.sources.grailed.browser.factory import create_browser_session_pool
from app.services.sources.grailed.discovery.client import DiscoveryHttpError
from app.services.sources.grailed.discovery.models import DiscoveryResult
from app.services.sources.grailed.discovery.service import (
    DiscoveryService,
    DiscoveryUnavailableError,
)
from app.services.transport.factory import create_http_transport, create_proxy_manager

router = APIRouter(prefix="/sources/grailed", tags=["sources"])


class DiscoveryRefreshRequest(BaseModel):
    force: bool = True


class SchemaAlertResponse(BaseModel):
    severity: Literal["info", "high"]
    kind: Literal["added", "removed", "type_changed"]
    path: str
    before: list[str]
    after: list[str]


class DiscoveryResponse(BaseModel):
    source: Literal["grailed"]
    status: Literal["ready", "stale", "discovering", "degraded", "unavailable"]
    method: Literal["intercept", "bundle", "manual"] | None = None
    discovered_at: datetime | None = None
    expires_at: datetime | None = None
    app_id: str | None = None
    active_index: str | None = None
    sold_index: str | None = None
    sorted_indices: list[str] = []
    brand_facet: str | None = None
    category_facet: str | None = None
    can_browse: bool = False
    key_acl: list[str] = []
    pagination_limit: int | None = None
    max_hits_per_page: int | None = None
    schema_sample_size: int = 0
    schema_field_count: int = 0
    drift_score: float = 0.0
    alerts: list[SchemaAlertResponse] = []

    @classmethod
    def from_result(cls, result: DiscoveryResult) -> DiscoveryResponse:
        return cls(
            source="grailed",
            status=result.status,
            method=result.method,
            discovered_at=result.discovered_at,
            expires_at=result.expires_at,
            app_id=result.app_id,
            active_index=result.active_index,
            sold_index=result.sold_index,
            sorted_indices=list(result.sorted_indices),
            brand_facet=result.brand_facet,
            category_facet=result.category_facet,
            can_browse=result.key_capabilities.can_browse,
            key_acl=list(result.key_capabilities.acl),
            pagination_limit=result.pagination_limit,
            max_hits_per_page=result.max_hits_per_page,
            schema_sample_size=result.schema_sample_size,
            schema_field_count=result.schema_field_count,
            drift_score=result.drift_score,
            alerts=[
                SchemaAlertResponse(
                    severity=alert.severity,
                    kind=alert.kind,
                    path=alert.path,
                    before=list(alert.before),
                    after=list(alert.after),
                )
                for alert in result.alerts
            ],
        )


async def get_discovery_service(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> AsyncIterator[DiscoveryService]:
    try:
        require_live_compliance(settings)
    except RuntimeError as exc:
        raise ApiError(
            503,
            str(exc),
            "Live mode requires compliance acknowledgement",
        ) from exc
    proxy = None
    if settings.proxy_enabled:
        proxy = create_proxy_manager(settings).select("grailed-discovery", pool="browser")
    transport = create_http_transport(settings, proxy=proxy)
    browser = (
        create_browser_session_pool(settings, proxy=proxy)
        if settings.source_mode == "live" and settings.fetch_tier_allow_browser
        else None
    )
    try:
        yield DiscoveryService(session, settings, transport, browser)
    finally:
        try:
            if browser is not None:
                await browser.close()
        finally:
            await transport.close()


@router.post("/discovery/refresh", response_model=DiscoveryResponse)
async def refresh_discovery(
    payload: DiscoveryRefreshRequest,
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> DiscoveryResponse:
    try:
        require_live_compliance(settings)
    except RuntimeError as exc:
        raise ApiError(503, str(exc), "Live mode requires compliance acknowledgement") from exc
    try:
        return DiscoveryResponse.from_result(await service.refresh(force=payload.force))
    except DiscoveryUnavailableError as exc:
        raise ApiError(503, "discovery_unavailable", "Grailed discovery is unavailable") from exc
    except DiscoveryHttpError as exc:
        raise ApiError(503, "discovery_failed", "Grailed discovery request failed") from exc


@router.get("/status", response_model=DiscoveryResponse)
async def source_status(
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
) -> DiscoveryResponse:
    result = await service.status()
    discovering = service.is_discovering()
    if result is None:
        return DiscoveryResponse(
            source="grailed", status="discovering" if discovering else "unavailable"
        )
    if discovering:
        result = replace(result, status="discovering")
    return DiscoveryResponse.from_result(result)
