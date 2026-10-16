"""P30, its smoothing towards the parent, and size P30 (v2 plan 2.2, 2.8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.metrics.calculator import MetricListing, compute_metrics
from app.services.metrics.p30 import p30_counts, smooth

AS_OF = datetime(2026, 10, 15, 12, tzinfo=UTC)


def _listing(
    item_id: int,
    *,
    created_days: int,
    sold_after: int | None = None,
    status: str | None = None,
    root: MetricListing | None = None,
    lot: bool = False,
    wholesale: bool = False,
    size: str | None = "M",
) -> MetricListing:
    listed = AS_OF - timedelta(days=created_days)
    start = root.item_created_at if root else listed
    sold_at = start + timedelta(days=sold_after) if sold_after is not None else None
    return MetricListing(
        id=item_id,
        status=status or ("sold" if sold_at else "active"),
        price=Decimal(100),
        sold_price=Decimal(100) if sold_at else None,
        item_created_at=start,
        listed_at=listed,
        sold_at=sold_at,
        exact_sale=True,
        price_usable=not lot,
        is_relist=root is not None,
        color=None,
        size=size,
        root_id=root.id if root else None,
        is_lot=lot,
        wholesale=wholesale,
    )


def test_the_cohort_is_chain_roots_listed_30_to_120_days_ago() -> None:
    removed_root = _listing(5, created_days=60, status="removed")
    items = [
        _listing(1, created_days=40, sold_after=10),
        _listing(2, created_days=100, sold_after=31),
        _listing(3, created_days=50),
        _listing(4, created_days=20, sold_after=5),
        removed_root,
        # The relist sold 20 days after its root was listed: the chain counts as sold.
        _listing(6, created_days=50, sold_after=20, root=removed_root),
        _listing(7, created_days=130, sold_after=3),
        _listing(8, created_days=45, sold_after=2, lot=True),
        _listing(9, created_days=45, sold_after=2, wholesale=True),
    ]

    assert p30_counts(items, AS_OF) == (2, 4)


def test_a_listing_younger_than_30_days_is_not_judged_yet() -> None:
    items = [_listing(1, created_days=40, sold_after=20)]

    assert p30_counts(items, AS_OF - timedelta(days=15)) == (0, 0)
    assert p30_counts(items, AS_OF - timedelta(days=5)) == (1, 1)


@pytest.mark.parametrize(
    ("sold", "cohort", "parent", "expected"),
    [
        # One sale out of one is not 100%: it leans on the 13.8% base rate.
        (1, 1, "0.138", "0.179048"),
        (402, 1051, "0.138", "0.377927"),
        (0, 0, "0.25", "0.250000"),
        (5, 5, "1", "1.000000"),
    ],
)
def test_smoothing_leans_on_the_parent(sold: int, cohort: int, parent: str, expected: str) -> None:
    assert smooth(sold, cohort, Decimal(parent)) == Decimal(expected)


def test_smoothing_stays_a_probability_and_approaches_the_parent_with_little_data() -> None:
    parent = Decimal("0.2")
    values = [smooth(k, n, parent) for n in range(0, 40, 3) for k in range(n + 1)]

    assert all(Decimal(0) <= value <= Decimal(1) for value in values)
    assert abs(smooth(1, 1, parent) - parent) < abs(smooth(10, 10, parent) - parent)


def test_size_rows_carry_their_p30_and_mark_the_sizes_that_sell() -> None:
    items = [
        *[_listing(i, created_days=60, sold_after=5, size="M") for i in range(1, 9)],
        *[_listing(i, created_days=60, size="M") for i in range(9, 13)],
        *[_listing(i, created_days=60, sold_after=5, size="L") for i in range(13, 15)],
        *[_listing(i, created_days=60, size="L") for i in range(15, 25)],
    ]

    sizes = {row["value"]: row for row in compute_metrics(items, AS_OF).sizes}

    # Group P30 = 10 / 24; M sells 8 of 12, L 2 of 12.
    assert sizes["m"]["p30"] == "0.666667" and sizes["m"]["p30_n"] == 12
    assert sizes["m"]["hot"] is True
    assert sizes["l"]["p30"] == "0.166667" and sizes["l"]["hot"] is False
