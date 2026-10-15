"""Daily metric snapshots (group_metrics_daily) and monthly sales (group_month_stats)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Base,
    Brand,
    GroupMetricDaily,
    GroupMonthStat,
    Listing,
    ListingModelAssignment,
    ModelGroup,
)
from app.services.metrics.calculator import MetricListing
from app.services.metrics.history import SNAPSHOT_KEEP_DAYS, month_stats, snapshot_rows
from app.services.metrics.pricing import percentile
from app.services.metrics.service import MetricsService

AS_OF = datetime(2026, 10, 15, 12, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parents[1]


def _sale(
    item_id: int,
    sold_at: datetime,
    price: str,
    *,
    new: bool = False,
    usable: bool = True,
) -> MetricListing:
    return MetricListing(
        id=item_id,
        status="sold",
        price=Decimal(price),
        sold_price=Decimal(price),
        item_created_at=sold_at - timedelta(days=10),
        listed_at=sold_at - timedelta(days=10),
        sold_at=sold_at,
        exact_sale=True,
        price_usable=usable,
        is_relist=False,
        color=None,
        size=None,
        new_condition=new,
    )


def test_percentile_interpolates_between_ranks() -> None:
    values = [Decimal(v) for v in ("10", "20", "30", "40")]

    assert percentile(values, Decimal("0.25")) == Decimal("17.50")
    assert percentile(values, Decimal("0.5")) == Decimal("25.00")
    assert percentile([Decimal(7)], Decimal("0.75")) == Decimal("7.00")
    assert percentile([], Decimal("0.5")) is None


def test_month_stats_split_by_condition_and_keep_lots_out_of_prices() -> None:
    september = datetime(2026, 9, 10, tzinfo=UTC)
    october = datetime(2026, 10, 2, tzinfo=UTC)
    items = [
        _sale(1, september, "100", new=True),
        _sale(2, september, "200"),
        _sale(3, september, "300"),
        _sale(4, september, "5000", usable=False),
        _sale(5, october, "150", new=True),
        _sale(6, october, "90"),
    ]

    rows = {(row.month, row.bucket): row for row in month_stats(items)}

    sept_all = rows[(date(2026, 9, 1), "all")]
    assert sept_all.sold_count == 4
    # GMV and percentiles leave the lot out; the count keeps it.
    assert sept_all.gmv == Decimal("600.00")
    assert (sept_all.p25, sept_all.p50, sept_all.p75) == (
        Decimal("150.00"),
        Decimal("200.00"),
        Decimal("250.00"),
    )
    assert rows[(date(2026, 9, 1), "new")].sold_count == 1
    assert rows[(date(2026, 9, 1), "used")].sold_count == 3
    assert rows[(date(2026, 10, 1), "new")].gmv == Decimal("150.00")
    assert rows[(date(2026, 10, 1), "used")].p50 == Decimal("90.00")


def test_month_stats_ignore_unsold_listings() -> None:
    active = _sale(1, AS_OF, "100")
    unsold = MetricListing(
        id=2,
        status="active",
        price=Decimal(100),
        sold_price=None,
        item_created_at=AS_OF,
        listed_at=AS_OF,
        sold_at=None,
        exact_sale=True,
        price_usable=True,
        is_relist=False,
        color=None,
        size=None,
    )

    rows = month_stats([active, unsold])

    assert {row.bucket for row in rows} == {"all", "used"}
    assert all(row.sold_count == 1 for row in rows)


def test_a_month_outlier_is_counted_but_left_out_of_prices() -> None:
    month = datetime(2026, 8, 5, tzinfo=UTC)
    items = [_sale(index, month, "100") for index in range(8)]
    items.append(_sale(99, month, "4000"))

    (row,) = [row for row in month_stats(items) if row.bucket == "all"]

    assert row.sold_count == 9
    assert row.gmv == Decimal("800.00") and row.p75 == Decimal("100.00")


def _metric(key: str, scope: str, *, sold_90d: int, active_now: int) -> dict[str, object]:
    return {
        "scope_key": key,
        "scope": scope,
        "brand_id": 1,
        "group_id": 10 if scope == "model" else None,
        "sold_30d": 0,
        "sold_90d": sold_90d,
        "active_now": active_now,
        "median_price_30d": None,
        "sell_through_30d": Decimal(0),
        "trend_score": None,
    }


def test_snapshots_keep_models_with_sales_or_a_real_supply_and_every_type_and_brand() -> None:
    values = [
        _metric("model:10", "model", sold_90d=1, active_now=0),
        _metric("model:11", "model", sold_90d=0, active_now=20),
        _metric("model:12", "model", sold_90d=0, active_now=19),
        _metric("type:1:bag", "type", sold_90d=0, active_now=0),
        _metric("brand:1", "brand", sold_90d=0, active_now=0),
    ]

    rows = snapshot_rows(values, date(2026, 10, 15))

    assert [row["scope_key"] for row in rows] == ["model:10", "model:11", "type:1:bag", "brand:1"]
    assert all(row["snapshot_date"] == date(2026, 10, 15) for row in rows)


async def _database(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Brand(
                id=1,
                name="Balenciaga",
                slug="balenciaga",
                aliases=[],
                include_subbrands=False,
                created_at=AS_OF,
                updated_at=AS_OF,
            )
        )
        for group_id, retired in ((10, None), (11, AS_OF)):
            session.add(
                ModelGroup(
                    id=group_id,
                    brand_id=1,
                    product_type="bag",
                    slug=f"g{group_id}",
                    name=f"Group {group_id}",
                    aliases=[],
                    status="auto",
                    source="mined",
                    retired_at=retired,
                    created_at=AS_OF,
                    updated_at=AS_OF,
                )
            )
        for index, (condition, days) in enumerate((("is_new", 5), ("is_used", 40), (None, 400))):
            sold_at = AS_OF - timedelta(days=days)
            session.add(
                Listing(
                    id=index + 1,
                    grailed_id=index + 1,
                    status="sold",
                    url=f"https://www.grailed.com/listings/{index + 1}",
                    title="Le Cagole bag",
                    brand_name_raw="Balenciaga",
                    brand_id=1,
                    product_type="bag",
                    price=Decimal(300),
                    sold_price=Decimal(300),
                    currency_original="USD",
                    condition=condition,
                    created_at=sold_at - timedelta(days=3),
                    sold_at=sold_at,
                    first_seen_at=sold_at,
                    last_seen_at=AS_OF,
                    photo_urls=[],
                    seller_identity_mode="none",
                    quality_flags=[],
                    fetch_tier="T1",
                    raw_json={},
                    schema_version=2,
                )
            )
            session.add(
                ListingModelAssignment(
                    listing_id=index + 1,
                    model_group_id=10,
                    method="test",
                    input_hash="x",
                    updated_at=AS_OF,
                )
            )
        await session.commit()
    return factory


async def _recompute(factory: async_sessionmaker[AsyncSession], as_of: datetime) -> None:
    async with factory() as session:
        await MetricsService(session).recompute([1], as_of=as_of)
        await session.commit()


async def _snapshots(factory: async_sessionmaker[AsyncSession]) -> list[tuple[date, str]]:
    async with factory() as session:
        rows = await session.execute(
            select(GroupMetricDaily.snapshot_date, GroupMetricDaily.scope_key).order_by(
                GroupMetricDaily.snapshot_date, GroupMetricDaily.scope_key
            )
        )
        return list(rows.tuples().all())


async def test_a_recompute_replaces_the_days_snapshot_and_keeps_earlier_days(
    tmp_path: Path,
) -> None:
    factory = await _database(tmp_path / "history.db")

    await _recompute(factory, AS_OF - timedelta(days=1))
    await _recompute(factory, AS_OF)
    await _recompute(factory, AS_OF)
    snapshots = await _snapshots(factory)

    yesterday, today = date(2026, 10, 14), date(2026, 10, 15)
    keys = ["brand:1", "model:10", "type:1:bag"]
    assert snapshots == [(yesterday, key) for key in keys] + [(today, key) for key in keys]
    async with factory() as session:
        model = await session.scalar(
            select(GroupMetricDaily).where(
                GroupMetricDaily.snapshot_date == today, GroupMetricDaily.scope_key == "model:10"
            )
        )
    assert model is not None
    assert (model.group_id, model.sold_30d, model.sold_90d) == (10, 1, 2)
    assert model.median_price_30d == Decimal("300.00") and model.p30 is None


async def test_snapshots_older_than_two_years_are_pruned(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "prune.db")

    await _recompute(factory, AS_OF - timedelta(days=SNAPSHOT_KEEP_DAYS + 1))
    await _recompute(factory, AS_OF - timedelta(days=SNAPSHOT_KEEP_DAYS - 1))
    await _recompute(factory, AS_OF)
    days = sorted({day for day, _ in await _snapshots(factory)})

    assert days == [(AS_OF - timedelta(days=SNAPSHOT_KEEP_DAYS - 1)).date(), AS_OF.date()]


async def test_month_stats_are_rebuilt_for_the_brand(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "months.db")

    await _recompute(factory, AS_OF)
    await _recompute(factory, AS_OF)
    async with factory() as session:
        rows = (
            await session.scalars(
                select(GroupMonthStat).where(GroupMonthStat.scope_key == "model:10")
            )
        ).all()

    stats = {(row.month, row.cond_bucket): row.sold_count for row in rows}
    assert stats == {
        (date(2026, 10, 1), "all"): 1,
        (date(2026, 10, 1), "new"): 1,
        (date(2026, 9, 1), "all"): 1,
        (date(2026, 9, 1), "used"): 1,
        (date(2025, 9, 1), "all"): 1,
        (date(2025, 9, 1), "used"): 1,
    }
    assert all(row.group_id == 10 and row.brand_id == 1 for row in rows)


def _alembic(database: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    return config


def _tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}


@pytest.mark.parametrize("table", ["group_metrics_daily", "group_month_stats"])
def test_the_migration_creates_and_drops_the_history_tables(tmp_path: Path, table: str) -> None:
    database = tmp_path / "migrate.db"
    config = _alembic(database)

    command.upgrade(config, "head")
    created = _tables(database)
    command.downgrade(config, "20261015_0022")

    assert table in created
    assert table not in _tables(database)
