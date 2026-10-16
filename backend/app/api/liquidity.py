"""The liquidity ranking settings of the trends screen; saving re-ranks at once."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.db.session import get_db
from app.services.metrics.calculator import PERIODS
from app.services.metrics.liquidity import (
    LiquidityPolicy,
    default_policy,
    known_sections,
    parse_policy,
)
from app.services.metrics.liquidity_store import (
    effective_policy,
    is_saved,
    reset_policy,
    save_policy,
)
from app.services.metrics.service import MetricsService

router = APIRouter(prefix="/settings/liquidity", tags=["settings"])


class LiquidityWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sales: Decimal = Field(ge=0)
    speed: Decimal = Field(ge=0)
    likes: Decimal = Field(ge=0)
    price: Decimal = Field(ge=0)


class LiquidityPolicyBody(BaseModel):
    """Weights are any non-negative shares; they are stored scaled to sum to 1."""

    model_config = ConfigDict(extra="forbid")

    period_days: int
    min_sales_per_month: Decimal = Field(ge=0, le=10_000)
    min_price_usd: int = Field(ge=0, le=1_000_000)
    excluded_sections: list[str] = Field(default_factory=list, max_length=50)
    weights: LiquidityWeights


class LiquiditySettings(LiquidityPolicyBody):
    saved: bool
    periods: list[int]
    defaults: LiquidityPolicyBody
    # Rows that got a score; set after a save or reset.
    ranked: int | None = None


@router.get("", response_model=LiquiditySettings)
async def read_liquidity_settings(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LiquiditySettings:
    return await _response(session)


@router.put("", response_model=LiquiditySettings)
async def update_liquidity_settings(
    payload: LiquidityPolicyBody,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LiquiditySettings:
    data = payload.model_dump(mode="json")
    data["version"] = default_policy().version
    try:
        policy = parse_policy(data, known_sections=known_sections())
    except ValueError as error:
        raise ApiError(422, "invalid_liquidity_policy", str(error)) from error
    await save_policy(session, policy)
    ranked = await MetricsService(session).rank()
    await session.commit()
    return await _response(session, ranked)


@router.delete("", response_model=LiquiditySettings)
async def reset_liquidity_settings(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LiquiditySettings:
    await reset_policy(session)
    ranked = await MetricsService(session).rank()
    await session.commit()
    return await _response(session, ranked)


async def _response(session: AsyncSession, ranked: int | None = None) -> LiquiditySettings:
    current = _body(await effective_policy(session))
    return LiquiditySettings(
        **current.model_dump(),
        saved=await is_saved(session),
        periods=list(PERIODS),
        defaults=_body(default_policy()),
        ranked=ranked,
    )


def _body(policy: LiquidityPolicy) -> LiquidityPolicyBody:
    weights = policy.weights
    return LiquidityPolicyBody(
        period_days=policy.period_days,
        min_sales_per_month=policy.min_sales_per_month,
        min_price_usd=int(policy.min_price),
        excluded_sections=sorted(policy.excluded_sections),
        weights=LiquidityWeights(
            sales=weights.sales, speed=weights.speed, likes=weights.likes, price=weights.price
        ),
    )
