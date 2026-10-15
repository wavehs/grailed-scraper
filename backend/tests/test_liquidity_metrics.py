"""Group metrics carry effective supply, P30 counts and price stats (v2 plan 2.1-2.3)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Base,
    Brand,
    GroupMetric,
    GroupMetricDaily,
    Listing,
    ListingModelAssignment,
    ModelGroup,
)
from app.services import per_brand
from app.services.metrics.service import MetricsService
from app.services.metrics.supply import WHOLESALE_MIN_ACTIVE
from app.services.per_brand import recompute_brands

AS_OF = datetime(2026, 10, 15, 12, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parents[1]
NEW_COLUMNS = {
    "active_effective",
    "active_fresh",
    "p30_sold",
    "p30_cohort",
    "p30",
    "gmv_90d",
    "pricing",
    "department",
    "liquidity_score",
    "ls_ref_scope",
}


def _listing(
    listing_id: int,
    *,
    status: str = "active",
    created_days: int = 10,
    sold_after: int | None = None,
    seller: str = "someone",
    flags: list[str] | None = None,
    department: str | None = "womenswear",
) -> Listing:
    created = AS_OF - timedelta(days=created_days)
    sold_at = created + timedelta(days=sold_after) if sold_after is not None else None
    return Listing(
        id=listing_id,
        grailed_id=listing_id,
        status="sold" if sold_at else status,
        url=f"https://www.grailed.com/listings/{listing_id}",
        title="Le Cagole bag",
        brand_name_raw="Balenciaga",
        brand_id=1,
        product_type="bag",
        price=Decimal(300),
        sold_price=Decimal(280) if sold_at else None,
        currency_original="USD",
        condition="is_used",
        created_at=created,
        sold_at=sold_at,
        first_seen_at=created,
        last_seen_at=AS_OF,
        photo_urls=[],
        seller_identity=seller,
        seller_identity_mode="hashed",
        quality_flags=flags or [],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
        price_initial=Decimal(400),
        department=department,
    )


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
        session.add(
            ModelGroup(
                id=10,
                brand_id=1,
                product_type="bag",
                slug="cagole",
                name="Cagole",
                aliases=[],
                status="auto",
                source="mined",
                created_at=AS_OF,
                updated_at=AS_OF,
            )
        )
        listings = [
            # Cohort: three of four sold within 30 days; one is menswear.
            _listing(1, created_days=40, sold_after=5),
            _listing(2, created_days=50, sold_after=10),
            _listing(3, created_days=60, sold_after=20, department="menswear"),
            _listing(4, created_days=70),
            _listing(5, created_days=200),
            _listing(6, flags=["lot_or_bundle"]),
            *(
                _listing(100 + index, seller="bulk", created_days=300)
                for index in range(WHOLESALE_MIN_ACTIVE)
            ),
        ]
        session.add_all(listings)
        session.add_all(
            ListingModelAssignment(
                listing_id=listing.id,
                model_group_id=10,
                method="test",
                input_hash="x",
                updated_at=AS_OF,
            )
            for listing in listings
        )
        await session.commit()
    return factory


async def _recompute(factory: async_sessionmaker[AsyncSession]) -> GroupMetric:
    async with factory() as session:
        await MetricsService(session).recompute([1], as_of=AS_OF)
        await session.commit()
        row = await session.scalar(select(GroupMetric).where(GroupMetric.scope_key == "model:10"))
    assert row is not None
    return row


async def test_a_recompute_fills_supply_p30_counts_and_prices(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "liquidity.db")

    row = await _recompute(factory)

    assert row.active_now == 3 + WHOLESALE_MIN_ACTIVE
    # The lot and the dormant wholesaler's stock leave; only the 10-day-old listing is fresh.
    assert (row.active_effective, row.active_fresh) == (2, 0)
    assert (row.p30_sold, row.p30_cohort) == (3, 4)
    assert row.gmv_90d == Decimal("840.00")
    assert row.pricing is not None and row.pricing["used"]["n"] == 3
    assert row.department == "womenswear"
    # Smoothing and the score need every brand: the cross-brand pass fills them.
    assert row.p30 is None and row.liquidity_score is None


async def test_a_recompute_keeps_the_last_cross_brand_values(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "carry.db")
    await _recompute(factory)
    async with factory() as session:
        await session.execute(
            update(GroupMetric)
            .where(GroupMetric.scope_key == "model:10")
            .values(p30=Decimal("0.4"), liquidity_score=Decimal("55.5"), ls_ref_scope="all")
        )
        await session.commit()

    row = await _recompute(factory)

    assert (row.p30, row.liquidity_score, row.ls_ref_scope) == (
        Decimal("0.400000"),
        Decimal("55.50"),
        "all",
    )


async def test_recomputing_brands_ranks_them_and_their_snapshots(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "ranked.db")

    async with factory() as session:
        await recompute_brands(session, None)
        row = await session.scalar(select(GroupMetric).where(GroupMetric.scope_key == "model:10"))
        snapshot = await session.scalar(
            select(GroupMetricDaily).where(GroupMetricDaily.scope_key == "model:10")
        )

    assert row is not None and snapshot is not None
    assert row.p30 is not None and Decimal(0) < row.p30 < Decimal(1)
    # Three sales in 90 days: no model reaches the reference sample, so no score.
    assert (row.liquidity_score, row.ls_ref_scope) == (Decimal(0), "none")
    assert (snapshot.p30, snapshot.liquidity_score) == (row.p30, row.liquidity_score)
    assert (snapshot.active_effective, snapshot.active_fresh) == (2, 0)


async def test_a_failed_season_rebuild_keeps_the_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = await _database(tmp_path / "seasons-fail.db")

    async def broken(*_: object, **__: object) -> int:
        raise RuntimeError("season profiles")

    monkeypatch.setattr(per_brand, "write_season_profiles", broken)
    async with factory() as session:
        with pytest.raises(RuntimeError, match="season profiles"):
            await recompute_brands(session, None)
    async with factory() as session:
        row = await session.scalar(select(GroupMetric).where(GroupMetric.scope_key == "model:10"))

    assert row is not None and row.p30 is not None and row.ls_ref_scope == "none"


def _columns(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {row[1] for row in connection.execute("PRAGMA table_info(group_metrics)")}


def test_the_migration_adds_and_drops_the_liquidity_columns(tmp_path: Path) -> None:
    database = tmp_path / "migrate.db"
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database.as_posix()}"

    command.upgrade(config, "20261015_0023")
    command.upgrade(config, "head")
    added = _columns(database)
    command.downgrade(config, "20261015_0023")
    dropped = _columns(database)

    assert NEW_COLUMNS <= added
    assert not NEW_COLUMNS & dropped
