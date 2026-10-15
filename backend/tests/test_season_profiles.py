"""Seasonal profiles are rebuilt from every sale, by covered months, after each recompute."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
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
    Listing,
    ListingModelAssignment,
    ModelGroup,
    SeasonProfile,
)
from app.services import per_brand
from app.services.metrics import season_profiles
from app.services.metrics.season_profiles import profiles_due, write_season_profiles
from app.services.metrics.seasonality import month_range
from app.services.per_brand import recompute_brands

AS_OF = datetime(2026, 10, 15, 12, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parents[1]


def _sale(
    listing_id: int,
    sold_at: datetime,
    *,
    brand_id: int = 1,
    product_type: str = "hoodie",
    department: str | None = "menswear",
) -> Listing:
    created = sold_at - timedelta(days=5)
    return Listing(
        id=listing_id,
        grailed_id=listing_id,
        status="sold",
        url=f"https://www.grailed.com/listings/{listing_id}",
        title="Hoodie",
        brand_name_raw="Rick Owens",
        brand_id=brand_id,
        product_type=product_type,
        price=Decimal(300),
        sold_price=Decimal(280),
        currency_original="USD",
        condition="is_used",
        created_at=created,
        sold_at=sold_at,
        first_seen_at=created,
        last_seen_at=AS_OF,
        photo_urls=[],
        seller_identity="someone",
        seller_identity_mode="hashed",
        quality_flags=[],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
        department=department,
    )


def _group(group_id: int, slug: str, *, retired: bool = False) -> ModelGroup:
    return ModelGroup(
        id=group_id,
        brand_id=1,
        product_type="hoodie",
        slug=slug,
        name=slug.title(),
        aliases=[],
        status="auto",
        source="mined",
        retired_at=AS_OF if retired else None,
        created_at=AS_OF,
        updated_at=AS_OF,
    )


def _brand_one_sales() -> list[Listing]:
    """Two hoodies a month from 9 Oct 2025, ten more in December; one womenswear parka."""

    sales = [_sale(1, datetime(2025, 10, 9, 17, tzinfo=UTC))]
    for index, month in enumerate(month_range(date(2025, 11, 1), date(2026, 9, 1))):
        sales += [
            _sale(10 + index * 2 + offset, datetime(month.year, month.month, day, tzinfo=UTC))
            for offset, day in enumerate((3, 17))
        ]
    sales += [_sale(100 + day, datetime(2025, 12, day, tzinfo=UTC)) for day in range(1, 11)]
    sales.append(
        _sale(200, datetime(2026, 1, 5, tzinfo=UTC), product_type="parka", department="womenswear")
    )
    # The running month never counts.
    sales.append(_sale(201, datetime(2026, 10, 2, tzinfo=UTC)))
    return sales


async def _database(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            Brand(
                id=brand_id,
                name=name,
                slug=name.lower().replace(" ", "-"),
                aliases=[],
                include_subbrands=False,
                created_at=AS_OF,
                updated_at=AS_OF,
            )
            for brand_id, name in ((1, "Rick Owens"), (2, "Golden Goose"))
        )
        session.add_all([_group(10, "dust"), _group(11, "_none"), _group(12, "old", retired=True)])
        sales = _brand_one_sales()
        # A brand collected later: March is its first, partial month.
        sales += [
            _sale(300, datetime(2026, 3, 20, tzinfo=UTC), brand_id=2),
            _sale(301, datetime(2026, 4, 2, tzinfo=UTC), brand_id=2),
        ]
        session.add_all(sales)
        await session.flush()
        groups = {listing.id: 10 for listing in sales if listing.brand_id == 1}
        groups[200] = 11
        groups[201] = 12
        session.add_all(
            ListingModelAssignment(
                listing_id=listing_id,
                model_group_id=group_id,
                method="test",
                input_hash="x",
                updated_at=AS_OF,
            )
            for listing_id, group_id in groups.items()
        )
        await session.commit()
    return factory


async def _profiles(factory: async_sessionmaker[AsyncSession]) -> dict[str, SeasonProfile]:
    async with factory() as session:
        rows = (await session.scalars(select(SeasonProfile))).all()
    return {row.profile_key: row for row in rows}


def _month(profile: SeasonProfile, month: int) -> dict[str, float | None]:
    return next(point for point in profile.months if point["month"] == month)


@pytest.fixture
def small_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(season_profiles, "BRAND_MIN_SALES_YEAR", 20)
    monkeypatch.setattr(season_profiles, "MODEL_MIN_SALES_YEAR", 10)


@pytest.mark.usefixtures("small_thresholds")
async def test_profiles_cover_the_market_types_brands_and_models(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "seasons.db")

    async with factory() as session:
        written = await write_season_profiles(session, AS_OF)
        await session.commit()
    profiles = await _profiles(factory)

    assert written == len(profiles)
    assert set(profiles) == {
        "market",
        "type:hoodie:*",
        "type:hoodie:menswear",
        "type:parka:*",
        "type:parka:womenswear",
        "brand:1:hoodie",
        "model:10",
    }
    hoodies = profiles["type:hoodie:menswear"]
    assert hoodies.months_covered == 11
    assert _month(hoodies, 12)["sales"] == 12.0
    assert _month(hoodies, 6)["sales"] == 2.0
    assert hoodies.seasons["winter"] is None
    assert hoodies.stable is None


@pytest.mark.usefixtures("small_thresholds")
async def test_a_later_brand_counts_only_from_its_first_complete_month(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "coverage.db")

    async with factory() as session:
        await write_season_profiles(session, AS_OF)
        await session.commit()
    market = (await _profiles(factory))["market"]

    # Brand 1 sells two a month; brand 2's March sale falls in its partial first month.
    assert _month(market, 3)["sales"] == 2.0
    assert _month(market, 4)["sales"] == 3.0


@pytest.mark.usefixtures("small_thresholds")
async def test_a_model_profile_blends_with_its_department_type(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "model.db")

    async with factory() as session:
        await write_season_profiles(session, AS_OF)
        await session.commit()
    model = (await _profiles(factory))["model:10"]

    assert model.kind == "model" and model.brand_id == 1 and model.group_id == 10
    assert model.department == "menswear"
    assert model.blend_weight is not None and Decimal(0) < model.blend_weight < Decimal(1)


async def test_small_brands_and_models_inherit_their_type(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "small.db")

    async with factory() as session:
        await write_season_profiles(session, AS_OF)
        await session.commit()
    kinds = {row.kind for row in (await _profiles(factory)).values()}

    assert kinds == {"market", "type"}


async def test_every_recompute_rebuilds_the_profiles(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "recompute.db")

    async with factory() as session:
        await recompute_brands(session, [2])
    profiles = await _profiles(factory)

    assert "market" in profiles and "type:hoodie:*" in profiles


async def test_a_partial_recompute_skips_fresh_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = await _database(tmp_path / "fresh.db")
    built: list[datetime] = []
    real = write_season_profiles

    async def recording(session: AsyncSession, as_of: datetime) -> int:
        built.append(as_of)
        return await real(session, as_of)

    monkeypatch.setattr(per_brand, "write_season_profiles", recording)
    async with factory() as session:
        await recompute_brands(session, [2])
        await recompute_brands(session, [2])
        assert len(built) == 1
        await recompute_brands(session, None)
        assert len(built) == 2
        await session.execute(update(SeasonProfile).values(computed_at=AS_OF - timedelta(days=400)))
        await session.commit()
        await recompute_brands(session, [1])

    assert len(built) == 3


async def test_profiles_are_due_when_missing_or_a_day_old(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "due.db")

    async with factory() as session:
        assert await profiles_due(session, AS_OF)
        await write_season_profiles(session, AS_OF)
        await session.commit()
        fresh = await profiles_due(session, AS_OF + timedelta(hours=23))
        stale = await profiles_due(session, AS_OF + timedelta(days=1))

    assert (fresh, stale) == (False, True)


@pytest.mark.usefixtures("small_thresholds")
async def test_a_model_belongs_to_the_brand_of_most_of_its_sales(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "majority.db")
    async with factory() as session:
        # A stray sale of another brand, stored last, must not move the model.
        stray = _sale(400, datetime(2026, 8, 3, tzinfo=UTC), brand_id=2)
        session.add(stray)
        await session.flush()
        session.add(
            ListingModelAssignment(
                listing_id=400, model_group_id=10, method="test", input_hash="x", updated_at=AS_OF
            )
        )
        await write_season_profiles(session, AS_OF)
        await session.commit()

    assert (await _profiles(factory))["model:10"].brand_id == 1


async def test_a_brand_with_a_stale_retired_assignment_recomputes_twice(
    tmp_path: Path,
) -> None:
    factory = await _database(tmp_path / "retired.db")

    async with factory() as session:
        await recompute_brands(session, [1])
        await recompute_brands(session, [1])
        keys = set((await session.scalars(select(GroupMetric.scope_key))).all())

    # Listing 201 still points at the retired group 12: it never gets a fresh row.
    assert "model:10" in keys and "model:12" not in keys


def _alembic(database: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    return config


def _tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}


def test_the_migration_creates_and_drops_the_profiles(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    config = _alembic(database)

    command.upgrade(config, "head")
    created = _tables(database)
    command.downgrade(config, "20261015_0024")
    dropped = _tables(database)

    assert "season_profiles" in created
    assert "season_profiles" not in dropped
