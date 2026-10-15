"""Raw, effective and fresh supply (v2 plan 2.1).

A dormant wholesaler is a seller with a big stock in a brand that almost never sells: their
listings stay in the groups (grouping is unchanged) but leave the supply denominators.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.metrics.calculator import MetricListing

WHOLESALE_MIN_ACTIVE = 200
WHOLESALE_MAX_SELL_THROUGH = Decimal("0.01")
WHOLESALE_SOLD_DAYS = 365
FRESH_DAYS = 60
_REMOVED = frozenset({"removed", "removed_pending"})

# (seller identity, status, sold_at) of one listing of the brand.
SellerRow = tuple[str | None, str, datetime | None]


@dataclass(frozen=True, slots=True)
class SupplyCounts:
    active_now: int
    active_effective: int
    active_fresh: int


def status_at(
    status: str,
    created_at: datetime,
    sold_at: datetime | None,
    removed_checked_at: datetime | None,
    moment: datetime,
) -> str | None:
    """The status a listing had at ``moment``; None if it was not listed yet (plan 2.7).

    A sale or a removal seen after ``moment`` leaves the listing active then. A removal
    with no check time is kept: it cannot be placed in time.
    """

    if created_at > moment:
        return None
    if status == "sold" and sold_at is not None and sold_at > moment:
        return "active"
    removed_later = removed_checked_at is not None and removed_checked_at > moment
    if status in _REMOVED and removed_later:
        return "active"
    return status


def dormant_wholesalers(rows: Iterable[SellerRow], as_of: datetime) -> frozenset[str]:
    """Sellers with ``active ≥ 200`` and ``sold_365 / (sold_365 + active) < 1%`` in a brand."""

    since = as_of - timedelta(days=WHOLESALE_SOLD_DAYS)
    active: Counter[str] = Counter()
    sold: Counter[str] = Counter()
    for seller, status, sold_at in rows:
        if seller is None:
            continue
        if status == "active":
            active[seller] += 1
        elif status == "sold" and sold_at is not None and since <= sold_at <= as_of:
            sold[seller] += 1
    return frozenset(
        seller
        for seller, stock in active.items()
        if stock >= WHOLESALE_MIN_ACTIVE
        and Decimal(sold[seller]) / Decimal(sold[seller] + stock) < WHOLESALE_MAX_SELL_THROUGH
    )


def is_effective(item: MetricListing) -> bool:
    """Counts towards real supply: not a lot and not a dormant wholesaler's listing."""

    return not item.is_lot and not item.wholesale


def is_fresh(item: MetricListing, as_of: datetime) -> bool:
    """Listed or bumped within the last 60 days."""

    return item.fresh_moment >= as_of - timedelta(days=FRESH_DAYS)


def supply_counts(listings: Sequence[MetricListing], as_of: datetime) -> SupplyCounts:
    active = [item for item in listings if item.status == "active"]
    effective = [item for item in active if is_effective(item)]
    return SupplyCounts(
        active_now=len(active),
        active_effective=len(effective),
        active_fresh=sum(is_fresh(item, as_of) for item in effective),
    )
