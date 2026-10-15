"""Price and bargaining by condition bucket, from the last 90 days of sales (v2 plan 2.3).

Replaces Ask-to-Bid: how much buyers knock off the last ask, how far the price fell from the
first ask, how fresh asks compare with sales, and how fast an item goes once the price is
right. Medians need five sales in the bucket; the API falls back to the parent ("≈").
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import TYPE_CHECKING, Any

from app.services.metrics.supply import is_effective, is_fresh

if TYPE_CHECKING:
    from app.services.metrics.calculator import MetricListing

BUCKETS = ("all", "new", "used")
QUARTILES = (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"))
PRICE_DAYS = 90
MIN_PRICE_SALES = 5
_CENT = Decimal("0.01")
_RATIO = Decimal("0.0001")
_DAY_SECONDS = Decimal(86_400)


def percentile(values: Sequence[Decimal], q: Decimal) -> Decimal | None:
    """Linear interpolation between closest ranks (``numpy.percentile``'s default)."""

    if not values:
        return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    value = ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return value.quantize(_CENT, ROUND_HALF_UP)


def in_bucket(item: MetricListing, bucket: str) -> bool:
    if bucket == "all":
        return True
    return item.new_condition == (bucket == "new")


def price_stats(
    listings: Sequence[MetricListing], as_of: datetime, outliers: frozenset[int]
) -> dict[str, dict[str, Any]]:
    """Per bucket: sales n and GMV, quartiles, discounts and the fresh-ask premium."""

    since = as_of - timedelta(days=PRICE_DAYS)
    sales = [
        item
        for item in listings
        if item.status == "sold"
        and item.sold_at is not None
        and since <= item.sold_at <= as_of
        and item.price_usable
        and item.id not in outliers
    ]
    asks = [
        item
        for item in listings
        if item.status == "active"
        and item.price_usable
        and is_effective(item)
        and is_fresh(item, as_of)
    ]
    return {
        bucket: _bucket(
            [item for item in sales if in_bucket(item, bucket)],
            [item.price for item in asks if in_bucket(item, bucket)],
        )
        for bucket in BUCKETS
    }


def _bucket(sales: list[MetricListing], asks: list[Decimal]) -> dict[str, Any]:
    prices = [item.sale_price for item in sales]
    enough = len(sales) >= MIN_PRICE_SALES
    p25, p50, p75 = (percentile(prices, q) if enough else None for q in QUARTILES)
    fresh_p50 = percentile(asks, QUARTILES[1]) if len(asks) >= MIN_PRICE_SALES else None
    premium = None
    if fresh_p50 is not None and p50:
        premium = (fresh_p50 / p50 - 1).quantize(_RATIO, ROUND_HALF_UP)
    return {
        "n": len(sales),
        "gmv": str(sum(prices, Decimal(0)).quantize(_CENT, ROUND_HALF_UP)),
        "p25": _text(p25),
        "p50": _text(p50),
        "p75": _text(p75),
        "offer_discount": _text(_median(sales, enough, _offer_discount, _RATIO)),
        "total_markdown": _text(_median(sales, enough, _total_markdown, _RATIO)),
        "days_at_final_price": _text(_median(sales, enough, _days_at_final_price, _CENT)),
        "fresh_n": len(asks),
        "fresh_ask_p50": _text(fresh_p50),
        "fresh_ask_premium": _text(premium),
    }


def _median(
    sales: list[MetricListing],
    enough: bool,
    measure: Callable[[MetricListing], Decimal | None],
    quantum: Decimal,
) -> Decimal | None:
    if not enough:
        return None
    values = [value for value in map(measure, sales) if value is not None]
    if len(values) < MIN_PRICE_SALES:
        return None
    return Decimal(median(values)).quantize(quantum, ROUND_HALF_UP)


def _offer_discount(item: MetricListing) -> Decimal | None:
    if item.sold_price is None or item.price <= 0:
        return None
    return 1 - item.sold_price / item.price


def _total_markdown(item: MetricListing) -> Decimal | None:
    if item.price_initial is None or item.price_initial <= 0:
        return None
    return 1 - item.sale_price / item.price_initial


def _days_at_final_price(item: MetricListing) -> Decimal | None:
    if not item.exact_sale or item.sold_at is None or item.price_updated_at is None:
        return None
    if item.price_updated_at > item.sold_at:
        return None
    return Decimal((item.sold_at - item.price_updated_at).total_seconds()) / _DAY_SECONDS


def _text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
