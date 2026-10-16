"""The liquidity policy edited on the trends screen, stored in app_settings."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting
from app.services.metrics.liquidity import (
    LiquidityPolicy,
    default_policy,
    known_sections,
    parse_policy,
    policy_data,
)

POLICY_KEY = "liquidity_policy"


async def effective_policy(session: AsyncSession) -> LiquidityPolicy:
    """The saved policy, or the defaults when none is saved or it no longer validates."""

    row = await session.get(AppSetting, POLICY_KEY)
    if row is None:
        return default_policy()
    try:
        return parse_policy(row.value, known_sections=known_sections())
    except (ValueError, AttributeError) as error:
        # A taxonomy change can retire a saved section; ranking must still work.
        structlog.get_logger(__name__).warning(
            "liquidity_policy_invalid", error=str(error), value=row.value
        )
        return default_policy()


async def is_saved(session: AsyncSession) -> bool:
    return await session.get(AppSetting, POLICY_KEY) is not None


async def save_policy(session: AsyncSession, policy: LiquidityPolicy) -> None:
    value = policy_data(policy)
    row = await session.get(AppSetting, POLICY_KEY)
    if row is None:
        session.add(AppSetting(key=POLICY_KEY, value=value, updated_at=datetime.now(UTC)))
    else:
        row.value = value
        row.updated_at = datetime.now(UTC)
    await session.flush()


async def reset_policy(session: AsyncSession) -> None:
    row = await session.get(AppSetting, POLICY_KEY)
    if row is not None:
        await session.delete(row)
        await session.flush()
