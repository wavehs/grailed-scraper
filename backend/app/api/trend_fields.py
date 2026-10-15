"""Liquidity and condition-price fields of a trend row (v2 plan 3.1, docs/METRICS.md §5-§8)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from app.db.models import GroupMetric
from app.domain.listings import decimal_to_cents
from app.services.metrics.liquidity import expected_days, rate

Condition = Literal["all", "new", "used"]
Department = Literal["menswear", "womenswear"]
LOW_DATA_SALES = 5
_RATIO = Decimal("0.0001")
# Statistics that need five sales; a bucket short of them borrows the parent's ("≈").
_MEDIANS = ("p25", "p50", "p75", "offer_discount", "total_markdown", "days_at_final_price")


class PriceBucket(BaseModel):
    """Sales of the last 90 days in one condition bucket; amounts in cents."""

    n: int
    gmv: int
    p25: int | None
    p50: int | None
    p75: int | None
    offer_discount: Decimal | None
    total_markdown: Decimal | None
    days_at_final_price: Decimal | None
    fresh_n: int
    fresh_ask_p50: int | None
    fresh_ask_premium: Decimal | None
    # The medians come from the parent scope: too few own sales.
    approximate: bool = False


class LiquidityFields(BaseModel):
    liquidity_score: Decimal | None = None
    p30: Decimal | None = None
    p30_raw: Decimal | None = None
    p30_n: int | None = None
    expected_days: Decimal | None = None
    active_effective: int | None = None
    active_fresh: int | None = None
    gmv_90d: int | None = None
    department: str | None = None
    price: PriceBucket | None = None


def liquidity_fields(
    metric: GroupMetric, condition: Condition, parent_pricing: dict[str, Any] | None
) -> dict[str, Any]:
    """Keyword arguments of ``TrendRow`` for the liquidity columns of ``metric``."""

    fields = LiquidityFields(
        liquidity_score=metric.liquidity_score,
        p30=metric.p30,
        p30_raw=rate(metric.p30_sold or 0, metric.p30_cohort or 0),
        p30_n=metric.p30_cohort,
        expected_days=expected_days(metric.p30) if metric.p30 is not None else None,
        active_effective=metric.active_effective,
        active_fresh=metric.active_fresh,
        gmv_90d=decimal_to_cents(metric.gmv_90d) if metric.gmv_90d is not None else None,
        department=metric.department,
        price=price_bucket(metric.pricing, condition, parent_pricing),
    )
    return dict(fields)


def price_bucket(
    pricing: dict[str, Any] | None,
    condition: Condition,
    parent_pricing: dict[str, Any] | None,
) -> PriceBucket | None:
    """The ``condition`` bucket; medians fall back to the parent's when the own are missing.

    ``None`` when the scope has neither sales nor fresh asks in the bucket.
    """

    own = (pricing or {}).get(condition)
    if not own or (not own.get("n") and not own.get("fresh_n")):
        return None
    values = dict(own)
    approximate = False
    parent = (parent_pricing or {}).get(condition) or {}
    if values.get("p50") is None and parent.get("p50") is not None:
        values.update({key: parent.get(key) for key in _MEDIANS})
        approximate = True
        if values.get("fresh_ask_p50") is not None:
            premium = Decimal(values["fresh_ask_p50"]) / Decimal(parent["p50"]) - 1
            values["fresh_ask_premium"] = premium.quantize(_RATIO)
    return PriceBucket(
        n=int(values.get("n") or 0),
        gmv=_cents(values.get("gmv")) or 0,
        p25=_cents(values.get("p25")),
        p50=_cents(values.get("p50")),
        p75=_cents(values.get("p75")),
        offer_discount=_decimal(values.get("offer_discount")),
        total_markdown=_decimal(values.get("total_markdown")),
        days_at_final_price=_decimal(values.get("days_at_final_price")),
        fresh_n=int(values.get("fresh_n") or 0),
        fresh_ask_p50=_cents(values.get("fresh_ask_p50")),
        fresh_ask_premium=_decimal(values.get("fresh_ask_premium")),
        approximate=approximate,
    )


def parent_key(metric: GroupMetric) -> str | None:
    """The scope whose prices stand in for a model's: its type within the brand."""

    if metric.scope != "model" or not metric.product_type:
        return None
    return f"type:{metric.brand_id}:{metric.product_type}"


def _cents(value: Any) -> int | None:
    return decimal_to_cents(Decimal(str(value))) if value is not None else None


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None
