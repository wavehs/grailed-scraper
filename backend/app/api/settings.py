"""Safe editable settings layered over environment configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mutation import MutationRoute
from app.core.config import Settings, get_settings
from app.db.models import AppSetting
from app.db.session import get_db

router = APIRouter(prefix="/settings", tags=["settings"], route_class=MutationRoute)
SettingOrigin = Literal["default", "env", "database"]

SETTING_GROUPS: dict[str, tuple[str, ...]] = {
    "collection": (
        "requests_per_minute",
        "max_concurrent_requests",
        "sold_history_days",
        "collect_price_min_usd",
        "collect_price_max_usd",
    ),
    "privacy": ("store_seller_identity",),
    "schedule": ("schedule_enabled", "schedule_hour", "schedule_rpm", "schedule_brand_ids"),
    "deals": (
        "deals_enabled",
        "deals_interval_minutes",
        "deals_scope",
        "deals_rpm",
        "deals_min_margin",
    ),
}
EDITABLE_SETTINGS = frozenset(key for keys in SETTING_GROUPS.values() for key in keys)
# Null clears an optional bound instead of being ignored.
NULLABLE_SETTINGS = frozenset(
    {"collect_price_min_usd", "collect_price_max_usd", "schedule_brand_ids"}
)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests_per_minute: int | None = Field(default=None, ge=1)
    max_concurrent_requests: int | None = Field(default=None, ge=1, le=3)
    sold_history_days: int | None = Field(default=None, ge=30, le=3650)
    collect_price_min_usd: int | None = Field(default=None, ge=0)
    collect_price_max_usd: int | None = Field(default=None, ge=1)
    store_seller_identity: Literal["none", "hashed", "plain"] | None = None
    schedule_enabled: bool | None = None
    schedule_hour: int | None = Field(default=None, ge=0, le=23)
    schedule_rpm: int | None = Field(default=None, ge=1)
    schedule_brand_ids: list[int] | None = None
    deals_enabled: bool | None = None
    deals_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    deals_scope: Literal["all", "watchlist"] | None = None
    deals_rpm: int | None = Field(default=None, ge=1)
    deals_min_margin: Decimal | None = Field(default=None, ge=0, le=10)
    confirm_plain_seller_identity: bool = False


class SettingResponse(BaseModel):
    value: Any
    origin: SettingOrigin
    # Value from .env or the code default, shown when a Settings override hides it.
    base_value: Any


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
            groups[group][key] = SettingResponse(
                value=getattr(settings, key), origin=origin, base_value=getattr(base, key)
            )
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
    request: Request,
) -> SettingsResponse:
    updates = {
        key: value
        # JSON mode: a Decimal is stored as its string, which Settings parses back.
        for key, value in payload.model_dump(
            mode="json", exclude_unset=True, exclude={"confirm_plain_seller_identity"}
        ).items()
        if value is not None or key in NULLABLE_SETTINGS
    }
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
    scanner = getattr(request.app.state, "deal_scanner", None)
    if scanner is not None and any(key.startswith("deals_") for key in updates):
        # Switching the scanner on takes effect now, not after its idle wait.
        scanner.wake()
    return await _response(session, base)
