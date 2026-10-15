"""Model badges: ❔ low data, 🔥 hot, 💎 stable, 🐢 slow, ⚠️ saturated (v2 plan 3.5)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.api.trend_fields import liquidity_fields
from app.db.models import GroupMetric
from app.services.metrics.badges import (
    HOT,
    LOW_DATA,
    SATURATED,
    SLOW,
    STABLE,
    BadgeRow,
    assign_badges,
    saturation,
    spread,
)

BACKEND = Path(__file__).resolve().parents[1]


def _sample(count: int = 10, *, section: str = "bags", start: int = 1) -> list[BadgeRow]:
    """Models whose score, days, P30 and supply rise together with the id."""

    return [
        BadgeRow(
            id=start + index,
            section=section,
            sold_90d=12,
            ls_ref_scope="all",
            liquidity_score=Decimal(10 * (index + 1)),
            p30=Decimal(index + 1) / 100,
            median_days_to_sell=Decimal(5 * (index + 1)),
            active_fresh=index,
        )
        for index in range(count)
    ]


def test_too_few_sales_give_only_the_low_data_badge() -> None:
    rows = [*_sample(), BadgeRow(id=99, section="bags", sold_90d=4, liquidity_score=Decimal(99))]

    badges = assign_badges(rows)

    assert badges[99] == [LOW_DATA]


def test_the_top_scores_are_hot_only_with_ten_sales() -> None:
    rows = _sample()
    rows[9] = replace(rows[9], sold_90d=9)

    badges = assign_badges(rows)

    # p90 of 10..100 is 91: only the top model reaches it, and it has nine sales.
    assert HOT not in badges[10]
    assert not any(HOT in codes for codes in badges.values())
    assert HOT in assign_badges(_sample())[10]


def test_a_tight_price_spread_with_thirty_sales_is_stable() -> None:
    tight = BadgeRow(id=50, section="bags", sold_90d=30, spread=Decimal("0.35"))
    loose = replace(tight, id=51, spread=Decimal("0.36"))
    thin = replace(tight, id=52, sold_90d=29)

    badges = assign_badges([tight, loose, thin])

    assert (STABLE in badges[50], STABLE in badges[51], STABLE in badges[52]) == (
        True,
        False,
        False,
    )


def test_slow_needs_long_days_and_a_low_p30_together() -> None:
    rows = _sample()
    # The slowest seller with the lowest P30 is slow; the slowest with a high P30 is not.
    rows[0] = replace(rows[0], median_days_to_sell=Decimal(100))

    badges = assign_badges(rows)

    assert SLOW in badges[1]
    assert SLOW not in badges[10]


def test_the_most_supplied_models_are_saturated() -> None:
    badges = assign_badges(_sample())

    # Fresh asks per month: 0, 0.25 ... 2.25; p80 is 1.8, reached by the top two.
    assert [row_id for row_id, codes in badges.items() if SATURATED in codes] == [9, 10]


def test_zero_supply_is_never_saturated() -> None:
    rows = [replace(row, active_fresh=0) for row in _sample()]

    assert not any(SATURATED in codes for codes in assign_badges(rows).values())


def test_a_section_scored_on_its_own_uses_its_own_thresholds() -> None:
    weak = [replace(row, ls_ref_scope="section") for row in _sample(section="shoes", start=1)]
    strong = [
        replace(row, liquidity_score=row.liquidity_score * 10 if row.liquidity_score else None)
        for row in _sample(section="bags", start=101)
    ]

    badges = assign_badges([*weak, *strong])

    # Against every model the shoes' best score of 100 is far below the p90 of 810.
    assert HOT in badges[10]
    assert HOT in badges[110]


def test_fallback_rows_get_no_badges_and_stay_out_of_the_sample() -> None:
    rows = [
        *_sample(),
        BadgeRow(id=77, section="bags", sold_90d=50, liquidity_score=Decimal(1000), fallback=True),
    ]

    badges = assign_badges(rows)

    assert badges[77] == []
    assert HOT in badges[10]


def test_saturation_and_spread_need_their_inputs() -> None:
    assert saturation(BadgeRow(id=1, section=None, sold_90d=6, active_fresh=4)) == Decimal(2)
    assert saturation(BadgeRow(id=1, section=None, sold_90d=0, active_fresh=4)) is None
    assert spread({"all": {"p25": "80.00", "p50": "100.00", "p75": "110.00"}}) == Decimal("0.3")
    assert spread({"all": {"p25": None, "p50": "100.00", "p75": "110.00"}}) is None
    assert spread({"all": {"p25": "0", "p50": "0", "p75": "0"}}) is None
    assert spread(None) is None


def test_trend_rows_carry_the_badges() -> None:
    metric = GroupMetric(badges=[HOT, STABLE], pricing=None)
    bare = GroupMetric(badges=None, pricing=None)

    assert liquidity_fields(metric, "all", None)["badges"] == [HOT, STABLE]
    assert liquidity_fields(bare, "all", None)["badges"] == []


def _columns(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {row[1] for row in connection.execute("PRAGMA table_info(group_metrics)")}


def test_the_migration_adds_and_drops_the_badges_column(tmp_path: Path) -> None:
    database = tmp_path / "badges.db"
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database.as_posix()}"

    command.upgrade(config, "head")
    added = _columns(database)
    command.downgrade(config, "20261015_0025")

    assert "badges" in added
    assert "badges" not in _columns(database)
