"""Cross-brand pass: P30 smoothing towards parents and the Liquidity Score (v2 plan 2.2, 2.4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.metrics.liquidity import smooth
from app.services.metrics.ranking import (
    RankRow,
    liquidity_score,
    rank,
    reference_scope,
)


def _row(
    row_id: int,
    scope: str,
    *,
    brand: int = 1,
    product_type: str | None = "bag",
    section: str | None = "bags",
    sold: int = 0,
    cohort: int = 0,
    sold_90d: int = 0,
    retired: bool = False,
    fallback: bool = False,
    previous: str | None = None,
) -> RankRow:
    return RankRow(
        id=row_id,
        scope=scope,
        brand_id=brand,
        product_type=product_type if scope != "brand" else None,
        section=section if scope != "brand" else None,
        p30_sold=sold,
        p30_cohort=cohort,
        sold_90d=sold_90d,
        retired=retired,
        fallback=fallback,
        ls_ref_scope=previous,
    )


@pytest.mark.parametrize(
    ("previous", "size", "expected"),
    [
        (None, 20, "section"),
        (None, 19, "all"),
        ("section", 18, "section"),
        ("section", 17, "all"),
        ("all", 21, "all"),
        ("all", 22, "section"),
        ("none", 22, "section"),
    ],
)
def test_the_reference_sample_switches_with_hysteresis(
    previous: str | None, size: int, expected: str
) -> None:
    assert reference_scope(previous, size) == expected


def test_the_score_is_a_weighted_geometric_mean_of_speed_and_depth() -> None:
    # V = 0.5, D = ln(4) / ln(16) = 0.5.
    assert liquidity_score(Decimal("0.2"), 3, Decimal("0.4"), Decimal(15)) == Decimal("50.00")
    assert liquidity_score(Decimal("0.9"), 40, Decimal("0.4"), Decimal(15)) == Decimal("100.00")
    assert liquidity_score(Decimal("0.9"), 0, Decimal("0.4"), Decimal(15)) == Decimal("0.00")


@pytest.mark.parametrize(("p30_ref", "sold_ref"), [("0", "15"), ("0.4", "0")])
def test_an_empty_reference_gives_no_score(p30_ref: str, sold_ref: str) -> None:
    assert liquidity_score(Decimal("0.2"), 3, Decimal(p30_ref), Decimal(sold_ref)) is None


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
    assert ranked[1].p30 == smooth(10, 50, overall)
    assert ranked[4].p30 == in_brand
    assert ranked[5].p30 == smooth(1, 1, in_brand)
    assert all(Decimal(0) <= item.p30 <= Decimal(1) for item in ranked.values())


def _models(section: str, count: int, *, start: int, sold_90d: int = 10) -> list[RankRow]:
    return [
        _row(start + index, "model", section=section, sold=index, cohort=20, sold_90d=sold_90d)
        for index in range(count)
    ]


def test_a_small_section_is_scored_against_every_section() -> None:
    rows = [
        _row(1, "brand", sold=100, cohort=400),
        *_models("tops", 25, start=100),
        *_models("bags", 5, start=200),
        _row(300, "model", section="bags", sold=5, cohort=10, sold_90d=10, retired=True),
    ]

    ranked = rank(rows)

    assert {ranked[index].ls_ref_scope for index in range(100, 125)} == {"section"}
    assert {ranked[index].ls_ref_scope for index in range(200, 205)} == {"all"}
    assert 300 not in ranked
    scores = [ranked[index].liquidity_score for index in range(100, 125)]
    assert all(score is not None and Decimal(0) <= score <= Decimal(100) for score in scores)
    assert max(score for score in scores if score is not None) == Decimal("100.00")


def _pass(count: int, previous: str | None) -> str | None:
    rows = [_row(1, "brand", sold=100, cohort=400)]
    rows += [
        _row(
            100 + index,
            "model",
            section="tops",
            sold=index,
            cohort=20,
            sold_90d=10,
            previous=previous,
        )
        for index in range(count)
    ]
    return rank(rows)[100].ls_ref_scope


def test_the_reference_choice_carries_over_from_the_last_pass() -> None:
    choice = _pass(25, None)
    history = [choice]
    # Each pass reads the choice the previous one stored, as ``ls_ref_scope`` does.
    for count in (19, 18, 17, 21, 22):
        choice = _pass(count, choice)
        history.append(choice)

    assert history == ["section", "section", "section", "all", "all", "section"]


def test_without_a_reference_the_score_is_zero_and_marked() -> None:
    rows = [_row(1, "brand", sold=1, cohort=10), _row(2, "model", sold=1, cohort=2, sold_90d=1)]

    ranked = rank(rows)

    assert (ranked[2].liquidity_score, ranked[2].ls_ref_scope) == (Decimal(0), "none")
    assert ranked[1].liquidity_score is None and ranked[1].ls_ref_scope is None
