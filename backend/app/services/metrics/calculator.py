"""Pure Decimal market metrics for one scope (model line/version, product type or brand)."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

WEEKS = 12
NEW_MODEL_DAYS = 60
NEW_LISTING_DAYS = 14
VARIANT_LIMIT = 12
# The periods the liquidity ranking can use; picked on the trends screen (docs/METRICS.md).
PERIODS = (7, 14, 30, 60, 90, 180, 365)
_CENT = Decimal("0.01")
_TENTH = Decimal("0.1")
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
    likes: int = 0

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
    # Per period in days ("30"): sold, median price, days to sell and likes of the sales.
    windows: dict[str, dict[str, object]] = field(default_factory=dict)
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
                days_30.append(_days_to_sell(item))
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
    result.windows = period_windows(listings, as_of)
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
    result.colors = variant_rows(variants, "color")
    result.sizes = variant_rows(variants, "size")
    return result


def period_windows(
    listings: Sequence[MetricListing], as_of: datetime
) -> dict[str, dict[str, object]]:
    """Sales of every period in ``PERIODS``; periods without a sale are left out.

    Medians are decimal strings: price of usable prices, days to sell of exact sales and
    likes of all the sales.
    """

    longest = timedelta(days=PERIODS[-1])
    sold = sorted(
        (
            (as_of - item.sold_at, item)
            for item in listings
            if item.status == "sold"
            and item.sold_at is not None
            and item.sold_at <= as_of
            and as_of - item.sold_at <= longest
        ),
        key=lambda pair: pair[0],
    )
    ages = [age for age, _ in sold]
    windows: dict[str, dict[str, object]] = {}
    for days in PERIODS:
        items = [item for _, item in sold[: bisect_right(ages, timedelta(days=days))]]
        if not items:
            continue
        windows[str(days)] = {
            "sold": len(items),
            "price": _text(_median([i.sale_price for i in items if i.price_usable], _CENT)),
            "days": _text(_median([_days_to_sell(i) for i in items if i.exact_sale], _CENT)),
            "likes": _text(_median([Decimal(i.likes) for i in items], _TENTH)),
        }
    return windows


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


def _days_to_sell(item: MetricListing) -> Decimal:
    assert item.sold_at is not None
    days = Decimal((item.sold_at - item.item_created_at).total_seconds()) / 86_400
    return max(days, Decimal(0))


def _text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _median(values: list[Decimal], quantum: Decimal) -> Decimal | None:
    if not values:
        return None
    return Decimal(median(values)).quantize(quantum, ROUND_HALF_UP)
