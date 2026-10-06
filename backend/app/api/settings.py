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
    "collection": (
        "requests_per_minute",
        "max_concurrent_requests",
        "sold_history_days",
        "collect_price_min_usd",
        "collect_price_max_usd",
    ),
    "privacy": ("store_seller_identity",),
    "compliance": ("live_compliance_acknowledged",),
}
EDITABLE_SETTINGS = frozenset(key for keys in SETTING_GROUPS.values() for key in keys)
# Null clears an optional bound instead of being ignored.
NULLABLE_SETTINGS = frozenset({"collect_price_min_usd", "collect_price_max_usd"})


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests_per_minute: int | None = Field(default=None, ge=1, le=90)
    max_concurrent_requests: int | None = Field(default=None, ge=1, le=3)
    sold_history_days: int | None = Field(default=None, ge=30, le=3650)
    collect_price_min_usd: int | None = Field(default=None, ge=0)
    collect_price_max_usd: int | None = Field(default=None, ge=1)
    store_seller_identity: Literal["none", "hashed", "plain"] | None = None
    live_compliance_acknowledged: bool | None = None
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
    updates = {
        key: value
        for key, value in payload.model_dump(
            exclude_unset=True, exclude={"confirm_plain_seller_identity"}
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
    return await _response(session, base)
