"""Raw, effective and fresh supply (v2 plan 2.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.services.metrics.calculator import MetricListing
from app.services.metrics.supply import SellerRow, dormant_wholesalers, supply_counts

AS_OF = datetime(2026, 10, 15, 12, tzinfo=UTC)


def _rows(seller: str, active: int, sold: int, sold_days: int = 10) -> list[SellerRow]:
    sold_at = AS_OF - timedelta(days=sold_days)
    return [(seller, "active", None)] * active + [(seller, "sold", sold_at)] * sold


def test_a_seller_with_a_big_stale_stock_is_a_dormant_wholesaler() -> None:
    rows = [
        *_rows("stale", 200, 1),
        *_rows("selling", 200, 3),
        *_rows("small", 199, 0),
        *_rows("old-sales", 250, 5, sold_days=400),
        *[(None, "active", None)] * 300,
    ]

    dormant = dormant_wholesalers(rows, AS_OF)

    # 1 / 201 < 1%; 3 / 203 is not; 199 active is below the stock floor; year-old sales
    # do not count, so a big stock with no sale this year is dormant.
    assert dormant == frozenset({"stale", "old-sales"})


def _active(
    item_id: int,
    *,
    listed_days: int = 10,
    bumped_days: int | None = None,
    lot: bool = False,
    wholesale: bool = False,
) -> MetricListing:
    listed = AS_OF - timedelta(days=listed_days)
    bumped = AS_OF - timedelta(days=bumped_days) if bumped_days is not None else None
    return MetricListing(
        id=item_id,
        status="active",
        price=Decimal(100),
        sold_price=None,
        item_created_at=listed,
        listed_at=listed,
        sold_at=None,
        exact_sale=True,
        price_usable=not lot,
        is_relist=False,
        color=None,
        size=None,
        is_lot=lot,
        wholesale=wholesale,
        fresh_at=max(listed, bumped) if bumped else listed,
    )


def test_effective_supply_drops_wholesalers_and_lots_and_fresh_supply_drops_stale_ones() -> None:
    items = [
        _active(1),
        _active(2, listed_days=200, bumped_days=5),
        _active(3, listed_days=200),
        _active(4, lot=True),
        _active(5, wholesale=True),
        _active(6, listed_days=60),
    ]

    counts = supply_counts(items, AS_OF)

    assert (counts.active_now, counts.active_effective, counts.active_fresh) == (6, 4, 3)
