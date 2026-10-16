"""Liquidity ranking: the threshold, weighted percentiles and the policy."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.services.metrics.liquidity import (
    LiquidityCandidate,
    LiquidityPolicy,
    Weights,
    is_eligible,
    liquidity_scores,
    load_liquidity_policy,
    parse_policy,
    policy_data,
    sales_per_month,
)

POLICY = LiquidityPolicy(
    version="test",
    period_days=30,
    min_sales_per_month=Decimal(5),
    min_price=Decimal(150),
    excluded_sections=frozenset({"dresses", "other"}),
    weights=Weights(
        sales=Decimal("0.40"), speed=Decimal("0.25"), likes=Decimal("0.20"), price=Decimal("0.15")
    ),
)


def _candidate(
    key: int,
    *,
    sold: int = 10,
    days: str | None = "10",
    likes: str | None = "30",
    price: str | None = "400",
    section: str | None = "footwear",
) -> LiquidityCandidate:
    return LiquidityCandidate(
        key=key,
        section=section,
        sold=sold,
        median_days_to_sell=Decimal(days) if days is not None else None,
        median_likes=Decimal(likes) if likes is not None else None,
        median_price=Decimal(price) if price is not None else None,
    )


@pytest.mark.parametrize(
    ("candidate", "eligible"),
    [
        (_candidate(1), True),
        (_candidate(2, sold=4), False),
        (_candidate(3, price="149.99"), False),
        (_candidate(4, price=None), False),
        (_candidate(5, days=None), False),
        (_candidate(6, section="dresses"), False),
        (_candidate(7, section=None), True),
        (_candidate(8, likes=None), True),
    ],
)
def test_threshold_keeps_only_frequent_expensive_items_with_known_speed(
    candidate: LiquidityCandidate, eligible: bool
) -> None:
    assert is_eligible(candidate, POLICY) is eligible


def test_threshold_scales_sales_of_the_period_to_a_month() -> None:
    quarter = LiquidityPolicy(
        version="test",
        period_days=90,
        min_sales_per_month=Decimal(5),
        min_price=Decimal(150),
        excluded_sections=frozenset(),
        weights=POLICY.weights,
    )

    # 15 sales in 90 days are 5 a month; 14 are 4.7.
    assert is_eligible(_candidate(1, sold=15), quarter)
    assert not is_eligible(_candidate(2, sold=14), quarter)
    assert sales_per_month(14, 90) == Decimal("4.7")
    assert sales_per_month(3, 7) == Decimal("12.9")


def test_no_sales_are_never_eligible_even_without_a_threshold() -> None:
    open_policy = LiquidityPolicy(
        version="test",
        period_days=30,
        min_sales_per_month=Decimal(0),
        min_price=Decimal(0),
        excluded_sections=frozenset(),
        weights=POLICY.weights,
    )

    assert not is_eligible(_candidate(1, sold=0), open_policy)


def test_score_is_weighted_percentile_of_sales_speed_likes_and_price() -> None:
    candidates = [
        _candidate(1, sold=50, days="5", likes="60", price="600"),
        _candidate(2, sold=20, days="15", likes="20", price="300"),
        _candidate(3, sold=8, days="30", likes="40", price="200"),
        _candidate(4, sold=3),  # below the threshold: no score
    ]

    scores = liquidity_scores(candidates, POLICY)

    # Percentiles over three: best 5/6, middle 3/6, worst 1/6.
    assert scores == {
        1: Decimal("83.33"),
        2: Decimal("43.33"),  # sales 3/6, speed 3/6, likes 1/6, price 3/6
        3: Decimal("23.33"),  # sales 1/6, speed 1/6, likes 3/6, price 1/6
    }


def test_ties_share_a_percentile_and_a_single_item_scores_fifty() -> None:
    tied = liquidity_scores([_candidate(1), _candidate(2)], POLICY)
    single = liquidity_scores([_candidate(1)], POLICY)

    assert tied == {1: Decimal("50.00"), 2: Decimal("50.00")}
    assert single == {1: Decimal("50.00")}


def test_unknown_likes_count_as_zero() -> None:
    scores = liquidity_scores(
        [_candidate(1, likes=None), _candidate(2, likes="1")], POLICY
    )

    assert scores[1] < scores[2]


def test_the_shipped_policy_matches_the_agreed_defaults() -> None:
    policy = load_liquidity_policy()

    assert policy.period_days == 30
    assert policy.min_sales_per_month == Decimal(5)
    assert policy.min_price == Decimal(150)
    assert policy.excluded_sections == frozenset({"dresses", "other"})
    assert policy.weights == POLICY.weights


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"weights": {"sales": 0, "speed": 0, "likes": 0, "price": 0}}, "above 0"),
        ({"weights": {"sales": 1.2, "speed": -0.2, "likes": 0, "price": 0}}, "negative"),
        ({"weights": {"sales": 1}}, "weights"),
        ({"weights": {"sales": "NaN", "speed": 1, "likes": 0, "price": 0}}, "number"),
        ({"period_days": 45}, "period_days"),
        ({"period_days": True}, "period_days"),
        ({"min_sales_per_month": -1}, "min_sales_per_month"),
        ({"min_price_usd": -1}, "min_price_usd"),
        ({"excluded_sections": ["dresses", "gowns"]}, "unknown section"),
    ],
)
def test_invalid_policy_fails_fast(patch: dict[str, object], message: str) -> None:
    data: dict[str, object] = {
        "version": "liquidity-v2",
        "period_days": 30,
        "min_sales_per_month": 5,
        "min_price_usd": 150,
        "excluded_sections": ["dresses"],
        "weights": {"sales": 0.4, "speed": 0.25, "likes": 0.2, "price": 0.15},
        **patch,
    }

    with pytest.raises(ValueError, match=message):
        parse_policy(data, known_sections={"dresses", "footwear", "other"})


def test_missing_policy_file_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="liquidity policy"):
        load_liquidity_policy(tmp_path / "missing.yaml")


def test_weights_are_scaled_to_sum_to_one() -> None:
    policy = parse_policy(
        {
            "period_days": 90,
            "min_sales_per_month": "2.5",
            "min_price_usd": 300,
            "excluded_sections": [],
            "weights": {"sales": 50, "speed": 30, "likes": 20, "price": 0},
        },
        known_sections={"footwear"},
    )

    assert policy.weights == Weights(
        sales=Decimal("0.5000"),
        speed=Decimal("0.3000"),
        likes=Decimal("0.2000"),
        price=Decimal("0.0000"),
    )
    assert policy.min_sales_per_month == Decimal("2.5")


def test_policy_data_round_trips() -> None:
    assert parse_policy(policy_data(POLICY), known_sections={"dresses", "other"}) == POLICY
