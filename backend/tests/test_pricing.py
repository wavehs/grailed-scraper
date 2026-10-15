"""Price and bargaining by condition bucket (v2 plan 2.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.services.metrics.calculator import MetricListing
from app.services.metrics.pricing import price_stats

AS_OF = datetime(2026, 10, 15, 12, tzinfo=UTC)


def _sale(
    item_id: int,
    sold: str,
    *,
    days: int = 10,
    ask: str = "125",
    initial: str | None = "200",
    final_days: int = 3,
    new: bool = False,
    usable: bool = True,
) -> MetricListing:
    sold_at = AS_OF - timedelta(days=days)
    return MetricListing(
        id=item_id,
        status="sold",
        price=Decimal(ask),
        sold_price=Decimal(sold),
        item_created_at=sold_at - timedelta(days=20),
        listed_at=sold_at - timedelta(days=20),
        sold_at=sold_at,
        exact_sale=True,
        price_usable=usable,
        is_relist=False,
        color=None,
        size=None,
        new_condition=new,
        price_initial=Decimal(initial) if initial else None,
        price_updated_at=sold_at - timedelta(days=final_days),
    )


def _ask(
    item_id: int, price: str, *, fresh_days: int = 5, lot: bool = False, wholesale: bool = False
) -> MetricListing:
    listed = AS_OF - timedelta(days=fresh_days)
    return MetricListing(
        id=item_id,
        status="active",
        price=Decimal(price),
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
        fresh_at=listed,
    )


def _items() -> list[MetricListing]:
    return [
        *(
            _sale(index, sold, final_days=index)
            for index, sold in enumerate(("100", "110", "120", "130", "140"), start=1)
        ),
        _sale(6, "300", new=True, ask="300", initial="300"),
        _sale(7, "999", days=120),
        _sale(8, "5000", usable=False),
        *(_ask(index, "150") for index in range(10, 15)),
        _ask(15, "1000", fresh_days=90),
        _ask(16, "10", lot=True),
        _ask(17, "10", wholesale=True),
    ]


def test_used_sales_give_every_price_metric() -> None:
    used = price_stats(_items(), AS_OF, frozenset())["used"]

    assert used["n"] == 5
    assert used["gmv"] == "600.00"
    assert (used["p25"], used["p50"], used["p75"]) == ("110.00", "120.00", "130.00")
    assert used["offer_discount"] == "0.0400"
    assert used["total_markdown"] == "0.4000"
    assert used["days_at_final_price"] == "3.00"
    assert used["fresh_n"] == 5 and used["fresh_ask_p50"] == "150.00"
    assert used["fresh_ask_premium"] == "0.2500"


def test_a_bucket_with_fewer_than_five_sales_keeps_counts_but_no_medians() -> None:
    stats = price_stats(_items(), AS_OF, frozenset())

    assert stats["new"]["n"] == 1 and stats["new"]["gmv"] == "300.00"
    assert stats["new"]["p50"] is None and stats["new"]["offer_discount"] is None
    assert stats["all"]["n"] == 6 and stats["all"]["p50"] == "125.00"


def test_scope_outliers_are_left_out() -> None:
    stats = price_stats(_items(), AS_OF, frozenset({5}))

    assert stats["used"]["n"] == 4 and stats["used"]["p50"] is None
