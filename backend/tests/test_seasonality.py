"""Seasonal profiles: complete months, absolute and relative indices, seasons, forecasts."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.services.metrics.seasonality import (
    blend,
    build_profile,
    complete_months,
    forecast_sales,
    month_range,
)

MONTHS = month_range(date(2025, 11, 1), date(2026, 9, 1))


def _flat(value: int, months: list[date]) -> dict[date, int]:
    return dict.fromkeys(months, value)


def test_the_first_and_the_running_month_are_incomplete() -> None:
    months = complete_months(
        datetime(2025, 10, 9, 17, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC)
    )

    assert months[0] == date(2025, 11, 1)
    assert months[-1] == date(2026, 9, 1)
    assert len(months) == 11


def test_a_sale_on_the_first_of_a_month_still_leaves_that_month_out() -> None:
    months = complete_months(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 4, 2, tzinfo=UTC))

    assert months == [date(2026, 2, 1), date(2026, 3, 1)]


def test_a_flat_type_has_every_index_at_one() -> None:
    profile = build_profile(_flat(10, MONTHS), MONTHS, _flat(100, MONTHS))

    assert [point.month for point in profile.months] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12]
    assert all(point.si_abs == pytest.approx(1.0) for point in profile.months)
    assert all(point.si_rel == pytest.approx(1.0) for point in profile.months)
    assert profile.sales_year == pytest.approx(120.0)
    assert profile.months_covered == 11


def test_a_november_peak_is_absolute_and_relative() -> None:
    counts = {**_flat(10, MONTHS), date(2025, 11, 1): 32}
    market = {**_flat(100, MONTHS), date(2025, 11, 1): 200}

    profile = build_profile(counts, MONTHS, market)
    november = next(point for point in profile.months if point.month == 11)
    june = next(point for point in profile.months if point.month == 6)

    # The mean month is (10 * 10 + 32) / 11 = 12.
    assert november.si_abs == pytest.approx(32 / 12)
    assert june.si_abs == pytest.approx(10 / 12)
    # Twice the market's month, but the market itself doubles: 1.6 times its share.
    assert november.si_rel == pytest.approx((32 / 132) / (200 / 1200))


def test_months_without_coverage_are_absent_and_missing_sales_are_zero() -> None:
    counts = {date(2026, 1, 1): 6}

    profile = build_profile(counts, MONTHS[:3], _flat(10, MONTHS))

    assert {point.month: point.sales for point in profile.months} == {11: 0.0, 12: 0.0, 1: 6.0}
    assert profile.months[0].si_abs == pytest.approx(3.0)


def test_two_years_of_a_calendar_month_are_averaged() -> None:
    months = month_range(date(2024, 10, 1), date(2026, 9, 1))
    counts = {**_flat(10, months), date(2024, 12, 1): 30, date(2025, 12, 1): 50}

    profile = build_profile(counts, months, _flat(10, months))
    december = next(point for point in profile.months if point.month == 12)

    assert december.sales == pytest.approx(40.0)


def test_a_season_needs_fifty_sales_and_averages_its_months() -> None:
    counts = {**_flat(20, MONTHS), date(2025, 11, 1): 40, date(2026, 6, 1): 2}
    small = build_profile(_flat(5, MONTHS), MONTHS, _flat(10, MONTHS))

    profile = build_profile(counts, MONTHS, _flat(10, MONTHS))
    autumn = profile.seasons["autumn"]

    assert autumn is not None
    # October is not covered: autumn is September and November.
    assert autumn.sales == pytest.approx(60.0)
    mean = sum(counts.values()) / 11
    assert autumn.si_abs == pytest.approx((20 / mean + 40 / mean) / 2)
    assert small.seasons["summer"] is None


def test_a_profile_repeating_its_shape_is_stable() -> None:
    months = month_range(date(2024, 10, 1), date(2026, 9, 1))
    shape = {month: 10 + (month.month % 4) * 5 for month in months}
    # The second year turns the shape upside down.
    late = set(months[12:])
    flipped = {month: 50 - value if month in late else value for month, value in shape.items()}

    stable = build_profile(shape, months, _flat(10, months))
    unstable = build_profile(flipped, months, _flat(10, months))
    short = build_profile(shape, MONTHS, _flat(10, MONTHS))

    assert stable.stable is True and stable.correlation == pytest.approx(1.0)
    assert unstable.stable is False
    assert short.stable is None and short.correlation is None


def test_a_model_profile_leans_to_its_type_by_sales() -> None:
    own = build_profile({**_flat(10, MONTHS), date(2026, 6, 1): 120}, MONTHS, _flat(10, MONTHS))
    parent = build_profile(_flat(10, MONTHS), MONTHS, _flat(10, MONTHS))

    blended = blend(own, parent, sales_year=300)
    june = next(point for point in blended.months if point.month == 6)
    own_june = next(point for point in own.months if point.month == 6)

    assert blended.blend_weight == pytest.approx(0.5)
    assert june.si_abs == pytest.approx((own_june.si_abs or 0) / 2 + 0.5)
    assert blended.sales_year == own.sales_year


def test_the_forecast_scales_recent_sales_by_the_target_season() -> None:
    profile = build_profile(
        {**_flat(10, MONTHS), date(2025, 12, 1): 30, date(2026, 1, 1): 30, date(2026, 2, 1): 30},
        MONTHS,
        _flat(10, MONTHS),
    )
    index = {point.month: point.si_abs or 0.0 for point in profile.months}

    # Mid-September: the last 30 days overlap August and September, both at the base level.
    expected = forecast_sales(12, index, (12, 1, 2), datetime(2026, 9, 15, tzinfo=UTC))

    assert expected == pytest.approx(36.0)


def test_a_window_without_a_complete_month_uses_the_neighbours() -> None:
    index = {9: 1.0, 11: 2.0, 12: 3.0}

    expected = forecast_sales(10, index, (12,), datetime(2026, 10, 31, 12, tzinfo=UTC))

    # October has no value: the window is the mean of September and November.
    assert expected == pytest.approx(10 * 3.0 / 1.5)


def test_no_forecast_without_the_target_months() -> None:
    assert forecast_sales(10, {9: 1.0}, (6, 7, 8), datetime(2026, 9, 20, tzinfo=UTC)) is None
    assert forecast_sales(10, {}, (6,), datetime(2026, 9, 20, tzinfo=UTC)) is None
