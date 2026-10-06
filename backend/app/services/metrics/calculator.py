"""Pure Decimal market metrics for one scope (model line/version, product type or brand)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

WEEKS = 12
NEW_MODEL_DAYS = 60
NEW_LISTING_DAYS = 14
MIN_TREND_SALES = 3
VARIANT_LIMIT = 12
_CENT = Decimal("0.01")
_RATIO = Decimal("0.0001")
_SHARE = Decimal("0.000001")
THIRTY = Decimal(30)


@dataclass(frozen=True, slots=True)
class MetricListing:
    """The facts metrics need about one listing; relists carry their item's first date."""

    id: int
    status: str
    price: Decimal
    sold_price: Decimal | None
    item_created_at: datetime
    listed_at: datetime
    sold_at: datetime | None
    exact_sale: bool
    price_usable: bool
    is_relist: bool
    color: str | None
    size: str | None

    @property
    def sale_price(self) -> Decimal:
        return self.sold_price or self.price


@dataclass(slots=True)
class ScopeMetrics:
    listings: int = 0
    sold_7d: int = 0
    sold_30d: int = 0
    sold_prev_30d: int = 0
    sold_90d: int = 0
    weekly_sales: list[int] = field(default_factory=lambda: [0] * WEEKS)
    weekly_median_price: list[Decimal | None] = field(default_factory=lambda: [None] * WEEKS)
    median_price_7d: Decimal | None = None
    median_price_30d: Decimal | None = None
    median_price_90d: Decimal | None = None
    price_change: Decimal | None = None
    median_days_to_sell: Decimal | None = None
    active_now: int = 0
    new_listings_14d: int = 0
    sell_through_30d: Decimal = Decimal(0)
    first_seen_at: datetime | None = None
    is_new: bool = False
    growth: Decimal = Decimal(1)
    speed: Decimal | None = None
    trend_score: Decimal | None = None
    colors: list[dict[str, object]] = field(default_factory=list)
    sizes: list[dict[str, object]] = field(default_factory=list)


def compute_metrics(listings: Sequence[MetricListing], as_of: datetime) -> ScopeMetrics:
    """All windows end at ``as_of``; see docs/METRICS.md for every formula."""

    result = ScopeMetrics(listings=len(listings))
    prices: dict[int, list[Decimal]] = {7: [], 30: [], 90: []}
    weekly_prices: list[list[Decimal]] = [[] for _ in range(WEEKS)]
    days_30: list[Decimal] = []
    variants: list[MetricListing] = []
    for item in listings:
        if result.first_seen_at is None or item.listed_at < result.first_seen_at:
            result.first_seen_at = item.listed_at
        if item.status == "active":
            result.active_now += 1
            variants.append(item)
        if not item.is_relist and as_of - item.listed_at <= timedelta(days=NEW_LISTING_DAYS):
            result.new_listings_14d += 1
        if item.status != "sold" or item.sold_at is None or item.sold_at > as_of:
            continue
        age = as_of - item.sold_at
        if age <= timedelta(days=90):
            result.sold_90d += 1
            variants.append(item)
            if item.price_usable:
                prices[90].append(item.sale_price)
        if age <= timedelta(days=30):
            result.sold_30d += 1
            if item.price_usable:
                prices[30].append(item.sale_price)
            if item.exact_sale:
                days = Decimal((item.sold_at - item.item_created_at).total_seconds()) / 86_400
                days_30.append(max(days, Decimal(0)))
        elif age <= timedelta(days=60):
            result.sold_prev_30d += 1
        if age <= timedelta(days=7):
            result.sold_7d += 1
            if item.price_usable:
                prices[7].append(item.sale_price)
        week = age.days // 7
        if week < WEEKS:
            result.weekly_sales[WEEKS - 1 - week] += 1
            if item.price_usable:
                weekly_prices[WEEKS - 1 - week].append(item.sale_price)
    result.weekly_median_price = [_median(values, _CENT) for values in weekly_prices]
    result.median_price_7d = _median(prices[7], _CENT)
    result.median_price_30d = _median(prices[30], _CENT)
    result.median_price_90d = _median(prices[90], _CENT)
    if result.median_price_30d is not None and result.median_price_90d:
        result.price_change = (
            (result.median_price_30d - result.median_price_90d) / result.median_price_90d
        ).quantize(_RATIO, ROUND_HALF_UP)
    result.median_days_to_sell = _median(days_30, _CENT)
    supply = result.sold_30d + result.active_now
    if supply:
        result.sell_through_30d = (Decimal(result.sold_30d) / Decimal(supply)).quantize(_SHARE)
    if result.first_seen_at is not None:
        result.is_new = as_of - result.first_seen_at <= timedelta(days=NEW_MODEL_DAYS)
    result.growth = (
        Decimal(result.sold_30d + 1) / Decimal(result.sold_prev_30d + 1)
    ).quantize(_RATIO, ROUND_HALF_UP)
    if result.median_days_to_sell is not None:
        result.speed = (THIRTY / (THIRTY + result.median_days_to_sell)).quantize(
            _RATIO, ROUND_HALF_UP
        )
    result.trend_score = trend_score(
        result.sold_30d, result.growth, result.speed, result.sell_through_30d
    )
    result.colors = variant_rows(variants, "color")
    result.sizes = variant_rows(variants, "size")
    return result


def trend_score(
    sold_30d: int, growth: Decimal, speed: Decimal | None, sell_through: Decimal
) -> Decimal | None:
    """growth × speed × sell-through × 100; unknown speed counts as 0.5 (a 30-day sale)."""

    if sold_30d < MIN_TREND_SALES:
        return None
    factor = speed if speed is not None else Decimal("0.5")
    return (growth * factor * sell_through * 100).quantize(_CENT, ROUND_HALF_UP)


def variant_rows(listings: Sequence[MetricListing], attribute: str) -> list[dict[str, object]]:
    """Sold (90 days) and active counts per color or size, best sellers first."""

    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item in listings:
        value = _variant_value(getattr(item, attribute))
        if value is None:
            continue
        counts[value][0 if item.status == "sold" else 1] += 1
    ranked = sorted(
        counts.items(),
        key=lambda pair: (
            -pair[1][0],
            -(Decimal(pair[1][0]) / Decimal(sum(pair[1]))),
            pair[0],
        ),
    )
    return [
        {
            "value": value,
            "sold": sold,
            "active": active,
            "sell_through": str((Decimal(sold) / Decimal(sold + active)).quantize(_SHARE)),
        }
        for value, (sold, active) in ranked[:VARIANT_LIMIT]
    ]


def _variant_value(value: str | None) -> str | None:
    normalized = " ".join((value or "").casefold().split())
    if not normalized or normalized in {"n/a", "na", "none", "unknown"}:
        return None
    return "gray" if normalized == "grey" else normalized


def _median(values: list[Decimal], quantum: Decimal) -> Decimal | None:
    if not values:
        return None
    return Decimal(median(values)).quantize(quantum, ROUND_HALF_UP)
