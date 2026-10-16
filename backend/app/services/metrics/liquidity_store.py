"""The liquidity policy edited on the trends screen, stored in app_settings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
# Invalid saved values already logged; every trends request reads the policy.
_warned: set[str] = set()


async def effective_policy(session: AsyncSession) -> LiquidityPolicy:
    """The saved policy, or the defaults when none is saved or it no longer validates."""

    return await saved_policy(session) or default_policy()


async def saved_policy(session: AsyncSession) -> LiquidityPolicy | None:
    """None when nothing is saved or the saved value no longer validates."""

    row = await session.get(AppSetting, POLICY_KEY)
    if row is None:
        return None
    try:
        return parse_policy(row.value, known_sections=known_sections())
    except (ValueError, AttributeError, TypeError, ArithmeticError) as error:
        # A taxonomy change can retire a saved section; ranking must still work.
        _warn_invalid(row.value, error)
        return None


def _warn_invalid(value: Any, error: Exception) -> None:
    key = repr(value)
    if key in _warned:
        return
    _warned.add(key)
    structlog.get_logger(__name__).warning(
        "liquidity_policy_invalid", error=str(error), value=value
    )


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
