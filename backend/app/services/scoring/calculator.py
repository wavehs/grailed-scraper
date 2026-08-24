"""Pure Decimal calculations for the market-v5 demand and liquidity model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any

ZERO = Decimal(0)
HUNDRED = Decimal(100)
TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")


@dataclass(slots=True)
class MetricDraft:
    group_id: int
    brand_id: int
    window_days: int
    active_count: int
    sold_count: int
    exact_sold_count: int
    median_sold_price: Decimal | None
    median_days_to_sell: Decimal | None
    median_sold_likes: Decimal | None
    sell_through: Decimal
    frequency_score: Decimal
    velocity_score: Decimal
    sell_through_score: Decimal
    likes_score: Decimal
    scoring_status: str
    confidence_score: Decimal
    confidence_factors: dict[str, str]
    quality_summary: dict[str, Any]
    variant_breakdown: dict[str, list[dict[str, Any]]]
    warnings: list[str]
    input_digest: str


@dataclass(frozen=True, slots=True)
class FinalMetrics:
    liquidity_score: Decimal | None
    demand_score: Decimal | None
    price_score: Decimal
    market_opportunity_score: Decimal | None
    component_breakdown: dict[str, dict[str, str]]


def decimal_median(values: list[Decimal]) -> Decimal | None:
    return Decimal(median(values)) if values else None


def ratio_score(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return ZERO
    return (Decimal(numerator) / Decimal(denominator) * HUNDRED).quantize(TWO_PLACES)


def frequency_score(sold_count: int, window_days: int) -> Decimal:
    monthly_sales = Decimal(sold_count) * Decimal(30) / Decimal(window_days)
    return _saturating_score(monthly_sales, Decimal(3))


def velocity_score(days: Decimal | None) -> Decimal:
    return _saturating_score(Decimal(30), days) if days is not None else ZERO


def engagement_score(likes: Decimal | None) -> Decimal:
    return _saturating_score(likes, Decimal(20)) if likes is not None else ZERO


def sample_sufficiency(sold_count: int, active_count: int, target: int = 20) -> Decimal:
    sold = min(Decimal(sold_count) / Decimal(target), Decimal(1))
    active = min(Decimal(active_count) / Decimal(target), Decimal(1))
    return ((sold + active) / Decimal(2) * HUNDRED).quantize(TWO_PLACES)


def confidence_score(
    *,
    sample: Decimal,
    coverage: Decimal,
    quality: Decimal,
    temporal: Decimal,
    degraded: bool,
    truncated: bool,
) -> Decimal:
    score = (
        sample * Decimal("0.40")
        + coverage * Decimal("0.35")
        + quality * Decimal("0.15")
        + temporal * Decimal("0.10")
    )
    if degraded:
        score *= Decimal("0.90")
    if truncated:
        score = min(score, Decimal(69))
    return min(max(score, ZERO), HUNDRED).quantize(TWO_PLACES, ROUND_HALF_UP)


def finalize_brand(drafts: list[MetricDraft]) -> dict[int, FinalMetrics]:
    result: dict[int, FinalMetrics] = {}
    for draft in drafts:
        components = {
            "sales_frequency": _component(draft.frequency_score, "0.50", "0.40"),
            "days_to_sell": _component(draft.velocity_score, "0.30", "0.10"),
            "sell_through": _component(draft.sell_through_score, "0.15", "0.20"),
            "sold_likes": _component(draft.likes_score, "0.05", "0.30"),
        }
        if draft.scoring_status != "scored":
            liquidity = demand = None
        else:
            monthly_sales = Decimal(draft.sold_count) * Decimal(30) / Decimal(draft.window_days)
            volume_cap = min(HUNDRED, monthly_sales / Decimal(3) * HUNDRED)
            liquidity = min(
                volume_cap,
                draft.frequency_score * Decimal("0.50")
                + draft.velocity_score * Decimal("0.30")
                + draft.sell_through_score * Decimal("0.15")
                + draft.likes_score * Decimal("0.05"),
            ).quantize(TWO_PLACES, ROUND_HALF_UP)
            demand = min(
                volume_cap,
                draft.frequency_score * Decimal("0.40")
                + draft.likes_score * Decimal("0.30")
                + draft.sell_through_score * Decimal("0.20")
                + draft.velocity_score * Decimal("0.10"),
            ).quantize(TWO_PLACES, ROUND_HALF_UP)
            components["volume_cap"] = {
                "score": str(volume_cap.quantize(TWO_PLACES, ROUND_HALF_UP)),
                "weight": "cap",
            }
        result[draft.group_id] = FinalMetrics(
            liquidity_score=liquidity,
            demand_score=demand,
            price_score=ZERO,
            market_opportunity_score=demand,
            component_breakdown=components,
        )
    return result


def _saturating_score(value: Decimal, pivot: Decimal) -> Decimal:
    if value <= ZERO:
        return ZERO
    return (value / (value + pivot) * HUNDRED).quantize(TWO_PLACES, ROUND_HALF_UP)


def _component(score: Decimal, liquidity_weight: str, demand_weight: str) -> dict[str, str]:
    return {
        "score": str(score),
        "liquidity_weight": liquidity_weight,
        "demand_weight": demand_weight,
    }
