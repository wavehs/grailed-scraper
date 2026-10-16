"""Liquidity score: how often, how fast, how liked and how expensive an item sells.

Pure functions; ``MetricsService`` feeds them all rows of one scope after a recompute.
See docs/METRICS.md, section 3.
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

POLICY_PATH = PROJECT_ROOT / "config" / "liquidity.yaml"
WEIGHT_NAMES = ("sales", "speed", "likes", "price")
_SCORE = Decimal("0.01")
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
    min_sales_30d: int
    min_price: Decimal
    excluded_sections: frozenset[str]
    weights: Weights


@dataclass(frozen=True, slots=True)
class LiquidityCandidate:
    """One metrics row; ``key`` is the row id."""

    key: int
    section: str | None
    sold_30d: int
    median_days_to_sell: Decimal | None
    median_likes: Decimal | None
    median_price: Decimal | None


def is_eligible(candidate: LiquidityCandidate, policy: LiquidityPolicy) -> bool:
    """Frequent, expensive enough, with a known speed and not in an excluded section."""

    return (
        candidate.sold_30d >= policy.min_sales_30d
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
    sales = _Ranks([Decimal(item.sold_30d) for item in pool])
    days = _Ranks([_known(item.median_days_to_sell) for item in pool])
    likes = _Ranks([_known(item.median_likes) for item in pool])
    prices = _Ranks([_known(item.median_price) for item in pool])
    weights = policy.weights
    return {
        item.key: (
            _HUNDRED
            * (
                weights.sales * sales.share(Decimal(item.sold_30d))
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
def liquidity_policy() -> LiquidityPolicy:
    """The shipped policy, read once per process."""

    return load_liquidity_policy()


def load_liquidity_policy(path: Path = POLICY_PATH) -> LiquidityPolicy:
    from app.services.grouping.policy import load_policy

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"Cannot read the liquidity policy {path}: {error}") from error
    if not isinstance(data, Mapping):
        raise ValueError(f"The liquidity policy {path} must be a mapping")
    return parse_policy(data, known_sections=load_policy().taxonomy.sections.keys())


def parse_policy(data: Mapping[str, Any], *, known_sections: Collection[str]) -> LiquidityPolicy:
    min_sales = data.get("min_sales_30d")
    if not isinstance(min_sales, int) or isinstance(min_sales, bool) or min_sales < 1:
        raise ValueError("min_sales_30d must be a whole number of at least 1")
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
        min_sales_30d=min_sales,
        min_price=min_price,
        excluded_sections=frozenset(sections),
        weights=_weights(data.get("weights")),
    )


def _weights(raw: object) -> Weights:
    if not isinstance(raw, Mapping) or set(raw) != set(WEIGHT_NAMES):
        raise ValueError(f"weights must name exactly: {', '.join(WEIGHT_NAMES)}")
    values = {name: _decimal(raw[name], f"weights.{name}") for name in WEIGHT_NAMES}
    if any(value < 0 for value in values.values()):
        raise ValueError("weights must not be negative")
    if sum(values.values()) != 1:
        raise ValueError("weights must sum to 1")
    return Weights(**values)


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{name} must be a number")
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a number") from error
