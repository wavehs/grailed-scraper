"""Grailed seller fees and a resale's net and ROI (v2 plan 2.6), from ``config/fees.yaml``.

``commission = max(minimum, c(base) · (E + ship_c))`` where ``ship_c`` is the buyer-paid
shipping unless a Grailed Label is used; ``processing = p · (E + ship_buyer) + fixed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import PROJECT_ROOT

FEES_PATH = PROJECT_ROOT / "config" / "fees.yaml"
# A sale is never counted as faster than a week (v2 plan, revision of 2.6).
MIN_DAYS_TO_SELL = Decimal(7)
_CENT = Decimal("0.01")
_RATIO = Decimal("0.0001")
_THIRTY = Decimal(30)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Tier(_Strict):
    from_usd: Decimal = Field(ge=0)
    rate: Decimal = Field(ge=0, lt=1)


class Commission(_Strict):
    tiers: tuple[Tier, ...]
    minimum_usd: Decimal = Field(ge=0)
    tier_base: Literal["price", "price_with_shipping"]

    @field_validator("tiers")
    @classmethod
    def _starts_at_zero(cls, tiers: tuple[Tier, ...]) -> tuple[Tier, ...]:
        if not tiers or min(tier.from_usd for tier in tiers) != 0:
            raise ValueError("commission tiers must include one starting at 0")
        return tuple(sorted(tiers, key=lambda tier: tier.from_usd))


class Processing(_Strict):
    fixed_usd: Decimal = Field(ge=0)
    domestic_rate: Decimal = Field(ge=0, lt=1)
    international_rate: Decimal = Field(ge=0, lt=1)


class FeeSchedule(_Strict):
    effective_from: date
    source: str
    commission: Commission
    processing: Processing

    def commission_rate(self, base: Decimal) -> Decimal:
        rate = self.commission.tiers[0].rate
        for tier in self.commission.tiers:
            if base >= tier.from_usd:
                rate = tier.rate
        return rate


@dataclass(frozen=True, slots=True)
class Fees:
    commission: Decimal
    processing: Decimal

    @property
    def total(self) -> Decimal:
        return self.commission + self.processing


@dataclass(frozen=True, slots=True)
class Resale:
    fees: Fees
    net: Decimal
    roi: Decimal


@lru_cache(maxsize=4)
def load_fees(path: Path = FEES_PATH) -> FeeSchedule:
    """Parse and validate the schedule once per process; invalid files raise ``ValueError``."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return FeeSchedule.model_validate(payload)


def grailed_fees(
    schedule: FeeSchedule,
    price: Decimal,
    *,
    ship_buyer: Decimal = Decimal(0),
    grailed_label: bool = False,
    international: bool = False,
) -> Fees:
    _require(price > 0, "price must be positive")
    _require(ship_buyer >= 0, "shipping cannot be negative")
    ship_commissioned = Decimal(0) if grailed_label else ship_buyer
    with_shipping = price + ship_commissioned
    base = price if schedule.commission.tier_base == "price" else with_shipping
    commission = max(
        schedule.commission.minimum_usd, schedule.commission_rate(base) * with_shipping
    )
    rates = schedule.processing
    rate = rates.international_rate if international else rates.domestic_rate
    processing = rate * (price + ship_buyer) + rates.fixed_usd
    return Fees(_cents(commission), _cents(processing))


def resale(
    schedule: FeeSchedule,
    *,
    buy: Decimal,
    costs_in: Decimal,
    expected: Decimal,
    ship_buyer: Decimal,
    label_cost: Decimal,
    grailed_label: bool = False,
    international: bool = False,
) -> Resale:
    """``net = E + ship_buyer − fees − label_cost``; ``ROI = net / (B + T_in) − 1``."""

    _require(min(buy, costs_in, label_cost) >= 0, "costs cannot be negative")
    _require(buy + costs_in > 0, "the purchase must cost something")
    fees = grailed_fees(
        schedule,
        expected,
        ship_buyer=ship_buyer,
        grailed_label=grailed_label,
        international=international,
    )
    net = expected + ship_buyer - fees.total - label_cost
    roi = (net / (buy + costs_in) - 1).quantize(_RATIO, ROUND_HALF_UP)
    return Resale(fees, _cents(net), roi)


def roi_per_30_days(roi: Decimal, days_to_sell: Decimal) -> Decimal:
    """ROI scaled to a 30-day turn: ``ROI · 30 / max(E[T], 7)``."""

    return (roi * _THIRTY / max(days_to_sell, MIN_DAYS_TO_SELL)).quantize(_RATIO, ROUND_HALF_UP)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, ROUND_HALF_UP)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)
