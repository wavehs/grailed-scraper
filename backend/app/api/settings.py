"""Safe editable settings layered over environment configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import AppSetting
from app.db.session import get_db

router = APIRouter(prefix="/settings", tags=["settings"])
SettingOrigin = Literal["default", "env", "database"]

SETTING_GROUPS: dict[str, tuple[str, ...]] = {
    "source": (
        "fetch_tier_preferred",
        "fetch_tier_allow_browser",
        "fetch_tier_allow_dom",
        "algolia_hits_per_page",
        "algolia_multiquery_batch_size",
        "algolia_pagination_strategy",
        "algolia_attributes_mode",
    ),
    "parser": (
        "parser_mode",
        "requests_per_minute",
        "max_concurrent_requests",
        "parser_request_delay_ms",
        "parser_request_timeout_s",
        "parser_max_retries",
        "parser_max_concurrency",
        "parser_max_items_per_brand",
        "identity_image_requests_per_run",
    ),
    "proxy": (
        "proxy_enabled",
        "proxy_rotation_mode",
        "proxy_allow_direct_fallback",
    ),
    "discovery": ("discovery_ttl_hours", "discovery_sample_size"),
    "privacy": ("store_seller_identity",),
}
EDITABLE_SETTINGS = frozenset(key for keys in SETTING_GROUPS.values() for key in keys)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fetch_tier_preferred: Literal["T1", "T2", "T3"] | None = None
    fetch_tier_allow_browser: bool | None = None
    fetch_tier_allow_dom: bool | None = None
    algolia_hits_per_page: int | None = Field(default=None, ge=1)
    algolia_multiquery_batch_size: int | None = Field(default=None, ge=1, le=8)
    algolia_pagination_strategy: Literal["auto", "browse", "keyset", "range_split"] | None = None
    algolia_attributes_mode: Literal["full", "lean"] | None = None
    parser_mode: Literal["delta", "full"] | None = None
    requests_per_minute: int | None = Field(default=None, ge=1, le=90)
    max_concurrent_requests: int | None = Field(default=None, ge=1, le=3)
    parser_request_delay_ms: int | None = Field(default=None, ge=1)
    parser_request_timeout_s: float | None = Field(default=None, ge=1)
    parser_max_retries: int | None = Field(default=None, ge=1)
    parser_max_concurrency: int | None = Field(default=None, ge=1, le=3)
    parser_max_items_per_brand: int | None = Field(default=None, ge=1)
    identity_image_requests_per_run: int | None = Field(default=None, ge=0, le=100)
    proxy_enabled: bool | None = None
    proxy_rotation_mode: Literal["round_robin", "random", "weighted"] | None = None
    proxy_allow_direct_fallback: bool | None = None
    discovery_ttl_hours: int | None = Field(default=None, ge=1)
    discovery_sample_size: int | None = Field(default=None, ge=1)
    store_seller_identity: Literal["none", "hashed", "plain"] | None = None
    confirm_plain_seller_identity: bool = False


class SettingResponse(BaseModel):
    value: Any
    origin: SettingOrigin


class SettingsResponse(BaseModel):
    groups: dict[str, dict[str, SettingResponse]]


async def effective_settings(session: AsyncSession, base: Settings) -> Settings:
    """Return validated settings with persisted safe overrides applied."""

    rows = list(
        await session.scalars(select(AppSetting).where(AppSetting.key.in_(EDITABLE_SETTINGS)))
    )
    values = base.model_dump()
    values.update({row.key: row.value for row in rows})
    return Settings(**values)


async def get_effective_settings(
    session: Annotated[AsyncSession, Depends(get_db)],
    base: Annotated[Settings, Depends(get_settings)],
) -> Settings:
    return await effective_settings(session, base)


async def _response(session: AsyncSession, base: Settings) -> SettingsResponse:
    rows = list(
        await session.scalars(select(AppSetting).where(AppSetting.key.in_(EDITABLE_SETTINGS)))
    )
    overrides = {row.key: row.value for row in rows}
    settings = await effective_settings(session, base)
    groups: dict[str, dict[str, SettingResponse]] = {}
    for group, keys in SETTING_GROUPS.items():
        groups[group] = {}
        for key in keys:
            origin: SettingOrigin
            if key in overrides:
                origin = "database"
            elif key in base.model_fields_set:
                origin = "env"
            else:
                origin = "default"
            groups[group][key] = SettingResponse(value=getattr(settings, key), origin=origin)
    return SettingsResponse(groups=groups)


@router.get("", response_model=SettingsResponse)
async def read_settings(
    session: Annotated[AsyncSession, Depends(get_db)],
    base: Annotated[Settings, Depends(get_settings)],
) -> SettingsResponse:
    return await _response(session, base)


@router.patch("", response_model=SettingsResponse)
async def update_settings(
    payload: SettingsPatch,
    session: Annotated[AsyncSession, Depends(get_db)],
    base: Annotated[Settings, Depends(get_settings)],
) -> SettingsResponse:
    updates = payload.model_dump(
        exclude_unset=True,
        exclude_none=True,
        exclude={"confirm_plain_seller_identity"},
    )
    current = await effective_settings(session, base)
    if (
        updates.get("store_seller_identity") == "plain"
        and current.store_seller_identity != "plain"
        and not payload.confirm_plain_seller_identity
    ):
        from app.api.errors import ApiError

        raise ApiError(
            409,
            "plain_seller_identity_confirmation_required",
            "Plain seller identity storage requires explicit confirmation",
        )
    # Re-validate the complete effective object so cross-field/config validators stay canonical.
    Settings(**{**current.model_dump(), **updates})
    now = datetime.now(UTC)
    for key, value in updates.items():
        row = await session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=value, updated_at=now))
        else:
            row.value = value
            row.updated_at = now
    await session.commit()
    return await _response(session, base)
