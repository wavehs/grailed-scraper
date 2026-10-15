from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.services.deals.rules import (
    FLOOR_RATIO,
    REASONS,
    DealRules,
    GroupFacts,
    Offer,
    bucket_of,
    judge,
)
from app.services.metrics.fees import load_fees, resale

SCHEDULE = load_fees()
RULES = DealRules()
GROUP = GroupFacts(
    slug="sisyphus",
    product_type="lowtop_sneakers",
    status="auto",
    source="seed",
    support=40,
    retired=False,
    is_fallback=False,
    liquidity_score=Decimal("70"),
    pricing={
        "used": {"n": 30, "p25": "350.00", "p50": "500.00"},
        "new": {"n": 6, "p25": "600.00", "p50": "700.00"},
    },
)
OFFER = Offer(
    price=Decimal("300"),
    shipping=Decimal("15"),
    condition="is_gently_used",
    quality_flags=(),
    seller_rating_avg=Decimal("4.9"),
    seller_rating_count=12,
    seller_trusted=False,
    wholesale=False,
)


def test_a_cheap_listing_of_a_liquid_trusted_model_from_a_rated_seller_is_a_deal() -> None:
    judgement = judge(OFFER, GROUP, RULES, SCHEDULE)

    expected = resale(
        SCHEDULE,
        buy=Decimal("300"),
        costs_in=Decimal("15"),
        expected=Decimal("500.00"),
        ship_buyer=Decimal(0),
        label_cost=Decimal(0),
    )
    assert judgement.is_deal, judgement.reasons
    assert (judgement.bucket, judgement.p25, judgement.p50) == (
        "used",
        Decimal("350.00"),
        Decimal("500.00"),
    )
    assert judgement.resale == expected
    assert expected.roi >= RULES.min_margin


@pytest.mark.parametrize(
    ("offer", "group", "reason"),
    [
        (OFFER, replace(GROUP, slug="_none"), "fallback"),
        (OFFER, replace(GROUP, is_fallback=True), "fallback"),
        (OFFER, replace(GROUP, slug="_desc-leather"), "fallback"),
        (OFFER, replace(GROUP, product_type="review"), "fallback"),
        (OFFER, replace(GROUP, status="ignored"), "ignored_group"),
        (OFFER, replace(GROUP, retired=True), "retired"),
        (OFFER, replace(GROUP, source="mined", support=3), "unproven_group"),
        (OFFER, replace(GROUP, liquidity_score=Decimal("49.99")), "illiquid"),
        (OFFER, replace(GROUP, liquidity_score=None), "illiquid"),
        (replace(OFFER, wholesale=True), GROUP, "wholesaler"),
        (replace(OFFER, seller_rating_count=4), GROUP, "unrated_seller"),
        (replace(OFFER, seller_rating_avg=Decimal("4.4")), GROUP, "unrated_seller"),
        (replace(OFFER, quality_flags=("no_photos",)), GROUP, "quality_flags"),
        (replace(OFFER, condition="is_new"), GROUP, "few_sales"),
        (replace(OFFER, price=Decimal("350.01")), GROUP, "above_p25"),
        (replace(OFFER, price=Decimal("149.99")), GROUP, "too_cheap"),
        (replace(OFFER, price=Decimal("340"), shipping=Decimal("40")), GROUP, "thin_margin"),
    ],
)
def test_each_rule_names_its_reason(offer: Offer, group: GroupFacts, reason: str) -> None:
    judgement = judge(offer, group, RULES, SCHEDULE)

    assert reason in judgement.reasons
    assert not judgement.is_deal
    assert set(judgement.reasons) <= set(REASONS)


def test_a_collab_line_can_be_a_deal() -> None:
    judgement = judge(OFFER, replace(GROUP, slug="_collab-reebok"), RULES, SCHEDULE)

    assert judgement.is_deal, judgement.reasons


def test_a_mined_model_is_proven_by_confirmation_or_support() -> None:
    mined = replace(GROUP, source="mined", support=3)

    assert judge(OFFER, replace(mined, status="confirmed"), RULES, SCHEDULE).is_deal
    assert judge(OFFER, replace(mined, support=10), RULES, SCHEDULE).is_deal


def test_a_trusted_seller_needs_no_ratings() -> None:
    unrated = replace(OFFER, seller_rating_count=0, seller_rating_avg=None, seller_trusted=True)

    assert judge(unrated, GROUP, RULES, SCHEDULE).is_deal


def test_the_floor_is_a_share_of_the_median() -> None:
    at_floor = replace(OFFER, price=Decimal("500.00") * FLOOR_RATIO)

    assert "too_cheap" not in judge(at_floor, GROUP, RULES, SCHEDULE).reasons


def test_missing_or_broken_prices_count_as_few_sales() -> None:
    broken = replace(GROUP, pricing={"used": {"n": 30, "p25": "n/a", "p50": None}})

    judgement = judge(OFFER, broken, RULES, SCHEDULE)

    assert judgement.reasons == ("few_sales",)
    assert judgement.resale is None


def test_tighter_rules_reject_what_the_defaults_accept() -> None:
    strict = DealRules(min_margin=Decimal("5"), min_bucket_sales=31)

    assert judge(OFFER, GROUP, strict, SCHEDULE).reasons == ("few_sales",)
    enough_sales = replace(strict, min_bucket_sales=20)
    assert "thin_margin" in judge(OFFER, GROUP, enough_sales, SCHEDULE).reasons


def test_conditions_map_to_the_metric_buckets() -> None:
    assert [bucket_of(value) for value in ("is_new", "is_used", "is_worn", None)] == [
        "new",
        "used",
        "used",
        "used",
    ]
