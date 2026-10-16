"""Liquidity score: how often, how fast, how liked and how expensive an item sells.

Pure functions; ``MetricsService`` feeds them all rows of one scope. The policy (period,
threshold, weights) is edited on the trends screen; config/liquidity.yaml holds the
defaults. See docs/METRICS.md, section 3.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from app.core.config import PROJECT_ROOT
from app.services.metrics.calculator import PERIODS

POLICY_PATH = PROJECT_ROOT / "config" / "liquidity.yaml"
WEIGHT_NAMES = ("sales", "speed", "likes", "price")
DAYS_PER_MONTH = Decimal(30)
_SCORE = Decimal("0.01")
_WEIGHT = Decimal("0.0001")
_MONTHLY = Decimal("0.1")
_HUNDRED = Decimal(100)
_HALF = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class Weights:
    sales: Decimal
    speed: Decimal
    likes: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class LiquidityPolicy:
    version: str
    period_days: int
    min_sales_per_month: Decimal
    min_price: Decimal
    excluded_sections: frozenset[str]
    weights: Weights


@dataclass(frozen=True, slots=True)
class LiquidityCandidate:
    """One metrics row over the policy period; ``key`` is the row id."""

    key: int
    section: str | None
    sold: int
    median_days_to_sell: Decimal | None
    median_likes: Decimal | None
    median_price: Decimal | None


def sales_per_month(sold: int, period_days: int) -> Decimal:
    return (Decimal(sold) * DAYS_PER_MONTH / Decimal(period_days)).quantize(
        _MONTHLY, ROUND_HALF_UP
    )


def is_eligible(candidate: LiquidityCandidate, policy: LiquidityPolicy) -> bool:
    """Frequent, expensive enough, with a known speed and not in an excluded section."""

    monthly = Decimal(candidate.sold) * DAYS_PER_MONTH / Decimal(policy.period_days)
    return (
        candidate.sold > 0
        and monthly >= policy.min_sales_per_month
        and candidate.median_price is not None
        and candidate.median_price >= policy.min_price
        and candidate.median_days_to_sell is not None
        and candidate.section not in policy.excluded_sections
    )


def liquidity_scores(
    candidates: Sequence[LiquidityCandidate], policy: LiquidityPolicy
) -> dict[int, Decimal]:
    """Weighted percentiles (0-100) of the eligible candidates; others get no score."""

    pool = [item for item in candidates if is_eligible(item, policy)]
    if not pool:
        return {}
    sales = _Ranks([Decimal(item.sold) for item in pool])
    days = _Ranks([_known(item.median_days_to_sell) for item in pool])
    likes = _Ranks([_known(item.median_likes) for item in pool])
    prices = _Ranks([_known(item.median_price) for item in pool])
    weights = policy.weights
    return {
        item.key: (
            _HUNDRED
            * (
                weights.sales * sales.share(Decimal(item.sold))
                + weights.speed * days.share(_known(item.median_days_to_sell), lower_is_better=True)
                + weights.likes * likes.share(_known(item.median_likes))
                + weights.price * prices.share(_known(item.median_price))
            )
        ).quantize(_SCORE, ROUND_HALF_UP)
        for item in pool
    }


class _Ranks:
    """Percentile of a value in a pool: worse values count fully, ties count half."""

    def __init__(self, values: list[Decimal]) -> None:
        self._sorted = sorted(values)
        self._size = Decimal(len(values))

    def share(self, value: Decimal, *, lower_is_better: bool = False) -> Decimal:
        low = bisect_left(self._sorted, value)
        high = bisect_right(self._sorted, value)
        worse = len(self._sorted) - high if lower_is_better else low
        return (Decimal(worse) + _HALF * (high - low)) / self._size


def _known(value: Decimal | None) -> Decimal:
    # Unknown likes count as none; days and price are known for every eligible row.
    return value if value is not None else Decimal(0)


@lru_cache(maxsize=1)
def default_policy() -> LiquidityPolicy:
    """The shipped defaults, read once per process."""

    return load_liquidity_policy()


def load_liquidity_policy(path: Path = POLICY_PATH) -> LiquidityPolicy:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"Cannot read the liquidity policy {path}: {error}") from error
    if not isinstance(data, Mapping):
        raise ValueError(f"The liquidity policy {path} must be a mapping")
    return parse_policy(data, known_sections=known_sections())


def known_sections() -> Collection[str]:
    from app.services.grouping.policy import load_policy

    return load_policy().taxonomy.sections.keys()


def parse_policy(data: Mapping[str, Any], *, known_sections: Collection[str]) -> LiquidityPolicy:
    period = data.get("period_days")
    # Strictly int: a stored 30.0 would miss the "30" window and empty the ranking.
    if type(period) is not int or period not in PERIODS:
        raise ValueError(f"period_days must be one of: {', '.join(map(str, PERIODS))}")
    min_sales = _decimal(data.get("min_sales_per_month"), "min_sales_per_month")
    if min_sales < 0:
        raise ValueError("min_sales_per_month must not be negative")
    min_price = _decimal(data.get("min_price_usd"), "min_price_usd")
    if min_price < 0:
        raise ValueError("min_price_usd must not be negative")
    sections = data.get("excluded_sections") or []
    if not isinstance(sections, list) or not all(isinstance(item, str) for item in sections):
        raise ValueError("excluded_sections must be a list of section ids")
    unknown = sorted(set(sections) - set(known_sections))
    if unknown:
        raise ValueError(f"excluded_sections has an unknown section: {', '.join(unknown)}")
    return LiquidityPolicy(
        version=str(data.get("version") or "liquidity"),
        period_days=period,
        min_sales_per_month=min_sales,
        min_price=min_price,
        excluded_sections=frozenset(sections),
        weights=_weights(data.get("weights")),
    )


def policy_data(policy: LiquidityPolicy) -> dict[str, Any]:
    """The JSON form ``parse_policy`` reads back; stored in app_settings."""

    weights = policy.weights
    return {
        "version": policy.version,
        "period_days": policy.period_days,
        "min_sales_per_month": str(policy.min_sales_per_month),
        "min_price_usd": str(policy.min_price),
        "excluded_sections": sorted(policy.excluded_sections),
        "weights": {name: str(getattr(weights, name)) for name in WEIGHT_NAMES},
    }


def _weights(raw: object) -> Weights:
    """Any non-negative shares; they are scaled to sum to 1."""

    if not isinstance(raw, Mapping) or set(raw) != set(WEIGHT_NAMES):
        raise ValueError(f"weights must name exactly: {', '.join(WEIGHT_NAMES)}")
    values = {name: _decimal(raw[name], f"weights.{name}") for name in WEIGHT_NAMES}
    if any(value < 0 for value in values.values()):
        raise ValueError("weights must not be negative")
    try:
        total = sum(values.values(), Decimal(0))
        if total <= 0:
            raise ValueError("at least one weight must be above 0")
        shares = {
            name: (value / total).quantize(_WEIGHT, ROUND_HALF_UP)
            for name, value in values.items()
        }
    except ArithmeticError as error:
        raise ValueError("weights are too large") from error
    # The largest share takes the rounding remainder so the weights sum to exactly 1.
    largest = max(WEIGHT_NAMES, key=lambda name: shares[name])
    remainder = Decimal(1) - sum(shares.values(), Decimal(0))
    return Weights(**{**shares, largest: shares[largest] + remainder})


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{name} must be a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ArithmeticError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be a number")
    return number
