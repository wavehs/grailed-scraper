"""Pure Decimal calculations for the opportunity-v1 scoring model."""

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
    """Metrics available before within-brand percentile components are assigned."""

    group_id: int
    brand_id: int
    active_count: int
    sold_count: int
    median_sold_price: Decimal | None
    median_days_to_sell: Decimal | None
    median_sold_likes_per_day: Decimal | None
    sell_through: Decimal
    sell_through_score: Decimal
    velocity_score: Decimal
    confidence_score: Decimal
    confidence_factors: dict[str, str]
    quality_summary: dict[str, Any]
    warnings: list[str]
    input_digest: str


@dataclass(frozen=True, slots=True)
class FinalMetrics:
    liquidity_score: Decimal
    price_score: Decimal
    likes_score: Decimal
    market_opportunity_score: Decimal
    component_breakdown: dict[str, dict[str, str]]


def decimal_median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return Decimal(median(values))


def ratio_score(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return ZERO
    return (Decimal(numerator) / Decimal(denominator) * HUNDRED).quantize(TWO_PLACES)


def velocity_score(days: Decimal | None, window_days: int) -> Decimal:
    if days is None:
        return ZERO
    bounded = min(max(days / Decimal(window_days), ZERO), Decimal(1))
    return ((Decimal(1) - bounded) * HUNDRED).quantize(TWO_PLACES, ROUND_HALF_UP)


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


def percentile_scores(
    values: dict[int, Decimal | None], *, reverse: bool = False
) -> dict[int, Decimal]:
    """Return midpoint-rank percentiles; missing values score zero, one value scores 50."""

    present = [(key, value) for key, value in values.items() if value is not None]
    if not present:
        return {key: ZERO for key in values}
    if len(present) == 1:
        only = present[0][0]
        return {key: Decimal(50) if key == only else ZERO for key in values}

    result: dict[int, Decimal] = {}
    ordered = sorted(value for _, value in present if value is not None)
    denominator = Decimal(len(ordered) - 1)
    for key, value in present:
        assert value is not None
        positions = [index for index, candidate in enumerate(ordered) if candidate == value]
        rank = Decimal(positions[0] + positions[-1]) / Decimal(2)
        score = rank / denominator * HUNDRED
        if reverse:
            score = HUNDRED - score
        result[key] = score.quantize(TWO_PLACES, ROUND_HALF_UP)
    for key in values:
        result.setdefault(key, ZERO)
    return result


def finalize_brand(drafts: list[MetricDraft]) -> dict[int, FinalMetrics]:
    prices = percentile_scores(
        {draft.group_id: draft.median_sold_price for draft in drafts}, reverse=True
    )
    likes = percentile_scores(
        {draft.group_id: draft.median_sold_likes_per_day for draft in drafts}
    )
    result: dict[int, FinalMetrics] = {}
    for draft in drafts:
        price = prices[draft.group_id]
        demand = likes[draft.group_id]
        liquidity = (
            (
                draft.sell_through_score * Decimal(40)
                + draft.velocity_score * Decimal(25)
            )
            / Decimal(65)
        ).quantize(TWO_PLACES, ROUND_HALF_UP)
        opportunity = (
            draft.sell_through_score * Decimal("0.40")
            + draft.velocity_score * Decimal("0.25")
            + demand * Decimal("0.20")
            + price * Decimal("0.15")
        ).quantize(TWO_PLACES, ROUND_HALF_UP)
        result[draft.group_id] = FinalMetrics(
            liquidity_score=liquidity,
            price_score=price,
            likes_score=demand,
            market_opportunity_score=opportunity,
            component_breakdown={
                "sell_through": {
                    "score": str(draft.sell_through_score),
                    "weight": "0.40",
                },
                "velocity": {"score": str(draft.velocity_score), "weight": "0.25"},
                "likes_per_day": {"score": str(demand), "weight": "0.20"},
                "price_affordability": {"score": str(price), "weight": "0.15"},
            },
        )
    return result
