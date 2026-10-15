"""Grailed seller fees and the reseller's net and ROI (v2 plan 2.6).

The same cases are mirrored by ``frontend/src/test/fees.test.ts``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.services.metrics.fees import FeeSchedule, grailed_fees, load_fees, resale, roi_per_30_days

D = Decimal


@pytest.fixture
def schedule() -> FeeSchedule:
    return load_fees()


@pytest.mark.parametrize(
    ("price", "ship", "label", "international", "commission", "processing"),
    [
        # 9% from $120, charged on buyer-paid shipping too without a Grailed Label.
        ("200", "15", False, False, "19.35", "7.99"),
        # 6% below $120.
        ("100", "10", False, False, "6.60", "4.33"),
        # The $1.99 minimum commission.
        ("20", "0", False, False, "1.99", "1.19"),
        # With a Grailed Label the commission skips shipping; processing never does.
        ("300", "15", True, False, "27.00", "11.48"),
        ("200", "0", False, True, "18.00", "10.47"),
        # The tier starts at exactly $120.
        ("120", "0", False, False, "10.80", "4.68"),
    ],
)
def test_fees_follow_the_schedule(
    schedule: FeeSchedule,
    price: str,
    ship: str,
    label: bool,
    international: bool,
    commission: str,
    processing: str,
) -> None:
    fees = grailed_fees(
        schedule, D(price), ship_buyer=D(ship), grailed_label=label, international=international
    )

    assert fees.commission == D(commission)
    assert fees.processing == D(processing)
    assert fees.total == D(commission) + D(processing)


def test_net_and_roi_of_a_resale(schedule: FeeSchedule) -> None:
    result = resale(
        schedule,
        buy=D("100"),
        costs_in=D("20"),
        expected=D("200"),
        ship_buyer=D("15"),
        label_cost=D("12"),
    )

    assert result.fees.total == D("27.34")
    assert result.net == D("175.66")
    assert result.roi == D("0.4638")


def test_roi_per_30_days_never_counts_a_sale_faster_than_a_week() -> None:
    assert roi_per_30_days(D("0.30"), D("60")) == D("0.1500")
    assert roi_per_30_days(D("0.30"), D("2")) == D("1.2857")


@pytest.mark.parametrize(
    "overrides",
    [
        {"buy": D("-1")},
        {"expected": D("0")},
        {"ship_buyer": D("-5")},
        {"buy": D(0), "costs_in": D(0)},
    ],
)
def test_impossible_inputs_are_rejected(
    schedule: FeeSchedule, overrides: dict[str, Decimal]
) -> None:
    arguments = {
        "buy": D("100"),
        "costs_in": D("0"),
        "expected": D("200"),
        "ship_buyer": D("0"),
        "label_cost": D("0"),
        **overrides,
    }

    with pytest.raises(ValueError):
        resale(
            schedule,
            buy=arguments["buy"],
            costs_in=arguments["costs_in"],
            expected=arguments["expected"],
            ship_buyer=arguments["ship_buyer"],
            label_cost=arguments["label_cost"],
        )


def test_a_schedule_without_tiers_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fees.yaml"
    path.write_text(
        "effective_from: 2026-05-20\nsource: x\n"
        "commission: {tiers: [], minimum_usd: '1.99', tier_base: price}\n"
        "processing: {fixed_usd: '0.49', domestic_rate: '0.0349', international_rate: '0.0499'}\n"
    )

    with pytest.raises(ValueError):
        load_fees(path)


def test_the_shipped_schedule_is_the_may_2026_one(schedule: FeeSchedule) -> None:
    assert str(schedule.effective_from) == "2026-05-20"
    assert schedule.commission_rate(D("119.99")) == D("0.06")
    assert schedule.commission_rate(D("120")) == D("0.09")
