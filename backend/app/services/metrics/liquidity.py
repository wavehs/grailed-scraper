"""P30, the chance an item sells within 30 days of being listed (v2 plan 2.2).

The cohort is relist chains whose root was listed 30-120 days ago; a chain succeeds when any
of its listings sold within 30 days of the root. Raw counts are smoothed towards a parent
rate, so one sale out of one is not 100%.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from app.services.metrics.supply import is_effective

if TYPE_CHECKING:
    from app.services.metrics.calculator import MetricListing

P30_DAYS = 30
COHORT_MIN_AGE_DAYS = 30
COHORT_MAX_AGE_DAYS = 120
SMOOTHING_WEIGHT = 20
MIN_EXPECTED_DAYS = 7
MAX_EXPECTED_DAYS = 365
_SURE = Decimal("0.999")
_SHARE = Decimal("0.000001")


def p30_counts(listings: Sequence[MetricListing], as_of: datetime) -> tuple[int, int]:
    """(chains sold within 30 days, chains in the cohort) for the scope's listings."""

    earliest = as_of - timedelta(days=COHORT_MAX_AGE_DAYS)
    latest = as_of - timedelta(days=COHORT_MIN_AGE_DAYS)
    cohort = {
        item.id
        for item in listings
        if item.root_id is None and is_effective(item) and earliest <= item.listed_at <= latest
    }
    window = timedelta(days=P30_DAYS)
    sold: set[int] = set()
    for item in listings:
        chain = item.root_id if item.root_id is not None else item.id
        if chain not in cohort or item.status != "sold" or item.sold_at is None:
            continue
        if item.sold_at <= as_of and item.sold_at - item.item_created_at <= window:
            sold.add(chain)
    return len(sold), len(cohort)


def p30_by(
    listings: Sequence[MetricListing], as_of: datetime, value: Callable[[MetricListing], Hashable]
) -> dict[Hashable, tuple[int, int]]:
    """``p30_counts`` per value (a size, say); a relist joins its root's value."""

    own = {item.id: value(item) for item in listings}
    split: dict[Hashable, list[MetricListing]] = defaultdict(list)
    for item in listings:
        root = item.root_id if item.root_id in own else item.id
        split[own[root]].append(item)
    return {key: p30_counts(items, as_of) for key, items in split.items()}


def rate(sold: int, cohort: int) -> Decimal | None:
    if cohort <= 0:
        return None
    return (Decimal(sold) / Decimal(cohort)).quantize(_SHARE, ROUND_HALF_UP)


def smooth(sold: int, cohort: int, parent: Decimal) -> Decimal:
    """Empirical Bayes: ``(k + m·p) / (n + m)`` with ``m = 20``."""

    weight = Decimal(SMOOTHING_WEIGHT)
    value = (Decimal(sold) + weight * parent) / (Decimal(cohort) + weight)
    return min(Decimal(1), max(Decimal(0), value)).quantize(_SHARE, ROUND_HALF_UP)


def expected_days(p30: Decimal) -> Decimal:
    """Days to sell at a constant hazard ``λ = −ln(1 − P̃30) / 30``, clamped to 7-365."""

    if p30 >= _SURE:
        return Decimal(MIN_EXPECTED_DAYS)
    if p30 <= 0:
        return Decimal(MAX_EXPECTED_DAYS)
    days = P30_DAYS / -math.log(1 - float(p30))
    clamped = min(MAX_EXPECTED_DAYS, max(MIN_EXPECTED_DAYS, days))
    return Decimal(str(round(clamped, 2)))
