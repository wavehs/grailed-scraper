"""Cross-brand pass: P30 smoothing towards parents (v2 plan 2.2)."""

from __future__ import annotations

from decimal import Decimal

from app.services.metrics.p30 import smooth
from app.services.metrics.ranking import RankRow, rank


def _row(
    row_id: int,
    scope: str,
    *,
    brand: int = 1,
    product_type: str | None = "bag",
    sold: int = 0,
    cohort: int = 0,
    retired: bool = False,
) -> RankRow:
    return RankRow(
        id=row_id,
        scope=scope,
        brand_id=brand,
        product_type=product_type if scope != "brand" else None,
        p30_sold=sold,
        p30_cohort=cohort,
        retired=retired,
    )


def test_p30_leans_on_type_in_brand_then_type_then_the_whole_market() -> None:
    rows = [
        _row(1, "brand", brand=1, sold=10, cohort=50),
        _row(2, "brand", brand=2, sold=0, cohort=0),
        _row(3, "type", brand=1, sold=10, cohort=20),
        _row(4, "type", brand=2, sold=0, cohort=0),
        _row(5, "model", brand=2, sold=1, cohort=1),
    ]

    ranked = rank(rows)

    overall = Decimal(10) / Decimal(50)
    across = smooth(10, 20, overall)
    in_brand = smooth(0, 0, across)
    assert ranked[1] == smooth(10, 50, overall)
    assert ranked[4] == in_brand
    assert ranked[5] == smooth(1, 1, in_brand)
    assert all(Decimal(0) <= value <= Decimal(1) for value in ranked.values())


def test_retired_groups_keep_their_frozen_p30() -> None:
    rows = [
        _row(1, "brand", sold=10, cohort=50),
        _row(2, "model", sold=5, cohort=10, retired=True),
    ]

    ranked = rank(rows)

    assert 2 not in ranked
    assert 1 in ranked
