"""Market fields that used to live only in ``raw_json`` get their own columns."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Listing, ListingPriceHistory, ParserRun
from app.domain.listings import ListingData, ListingStatus
from app.repositories.listings import ListingRepository
from app.services.normalization.backfill import backfill_market_fields
from app.services.normalization.fx import StaticFxRateProvider
from app.services.normalization.mapping import load_source_mapping
from app.services.normalization.market import MARKET_COLUMNS
from app.services.normalization.normalizer import ListingNormalizer, NormalizationContext
from app.services.sources.base.models import RawHit

OBSERVED = datetime(2026, 10, 15, 12, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parents[1]

# Trimmed real hits from the live index (ids and usernames removed).
ACTIVE_HIT: dict[str, Any] = {
    "objectID": "501",
    "title": "Archive bomber",
    "designers": [{"name": "Rick Owens", "slug": "rick-owens"}],
    "price": 450,
    "price_drops": [500, 450],
    "price_updated_at": "2016-10-24T11:52:44.947Z",
    "bumped_at": "2016-10-24T11:55:05.644Z",
    "sold_shipping_price": None,
    "shipping": {"us": {"amount": 15.99, "enabled": True}, "eu": {"amount": 25, "enabled": True}},
    "makeoffer": 1,
    "buynow": 0,
    "department": "menswear",
    "user": {
        "total_bought_and_sold": 0,
        "seller_score": {"rating_average": None, "rating_count": 0},
        "trusted_seller": False,
    },
}
SOLD_HIT: dict[str, Any] = {
    "objectID": "502",
    "title": "Le Cagole bag",
    "designers": [{"name": "Balenciaga", "slug": "balenciaga"}],
    "price": 353,
    "sold_price": 350,
    "price_drops": [600, 540, 486, 437, 393, 353],
    "price_updated_at": "2026-01-28T10:28:47.802Z",
    "bumped_at": "2024-12-05T04:59:54.183Z",
    "sold_shipping_price": 0.0,
    "shipping": {"us": {"amount": 0.0, "enabled": True}},
    "makeoffer": 1,
    "buynow": 0,
    "department": "womenswear",
    "user": {
        "total_bought_and_sold": 88,
        "seller_score": {"rating_average": 4.176470588235294, "rating_count": 17},
        "trusted_seller": True,
    },
}


async def _normalize(
    payload: dict[str, Any],
    status: ListingStatus = "active",
    rates: StaticFxRateProvider | None = None,
) -> ListingData:
    result = await ListingNormalizer(load_source_mapping(), rates).normalize(
        RawHit(payload, "T1"),
        NormalizationContext(status=status, parser_run_id=1, observed_at=OBSERVED),
    )
    assert result.listing is not None, result.failures
    return result.listing


async def test_an_active_hit_keeps_its_market_fields() -> None:
    listing = await _normalize(ACTIVE_HIT)

    assert listing.price_initial == Decimal("500.00")
    assert listing.price_drop_count == 1
    assert listing.price_updated_at == datetime(2016, 10, 24, 11, 52, 44, 947000, tzinfo=UTC)
    assert listing.bumped_at == datetime(2016, 10, 24, 11, 55, 5, 644000, tzinfo=UTC)
    assert listing.sold_shipping_price is None
    assert listing.ship_domestic == Decimal("15.99")
    assert listing.makeoffer is True and listing.buynow is False
    assert listing.department == "menswear"
    assert listing.seller_rating_avg is None
    assert listing.seller_rating_count == 0
    assert listing.seller_txn_count == 0
    assert listing.seller_trusted is False


async def test_a_sold_hit_keeps_its_market_fields() -> None:
    listing = await _normalize(SOLD_HIT, status="sold")

    assert listing.price_initial == Decimal("600.00")
    assert listing.price_drop_count == 5
    assert listing.sold_shipping_price == Decimal("0.00")
    assert listing.ship_domestic == Decimal("0.00")
    assert listing.department == "womenswear"
    assert listing.seller_rating_avg == Decimal("4.176")
    assert listing.seller_rating_count == 17
    assert listing.seller_txn_count == 88
    assert listing.seller_trusted is True


async def test_drops_without_the_current_price_still_count_the_last_drop() -> None:
    listing = await _normalize({**ACTIVE_HIT, "price": 995, "price_drops": [1245]})

    assert listing.price_initial == Decimal("1245.00")
    assert listing.price_drop_count == 1


async def test_a_listing_without_drops_starts_at_its_price() -> None:
    hit = {key: value for key, value in ACTIVE_HIT.items() if key != "user"}
    listing = await _normalize({**hit, "price_drops": []})

    assert listing.price_initial == Decimal("450.00")
    assert listing.price_drop_count == 0
    assert listing.seller_rating_count is None and listing.seller_trusted is None


async def test_foreign_prices_are_converted_like_the_price() -> None:
    rates = StaticFxRateProvider({("EUR", date(2026, 10, 15)): Decimal("1.20")})
    listing = await _normalize({**ACTIVE_HIT, "currency": "EUR"}, rates=rates)

    assert listing.price == Decimal("540.00")
    assert listing.price_initial == Decimal("600.00")
    assert listing.ship_domestic == Decimal("19.19")


@pytest.mark.parametrize("value", ["1", "true", True, 1])
async def test_truthy_source_flags_are_true(value: object) -> None:
    listing = await _normalize({**ACTIVE_HIT, "buynow": value})

    assert listing.buynow is True


async def _database(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            ParserRun(
                id=1,
                source="grailed",
                mode="delta",
                status="running",
                dry_run=False,
                degraded_mode=False,
                requests_made=0,
                warnings=[],
                stats={},
                created_at=OBSERVED,
            )
        )
        await session.commit()
    return factory


async def test_a_first_insert_records_the_base_price(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "history.db")
    listing = await _normalize(ACTIVE_HIT)
    async with factory() as session:
        repository = ListingRepository(session)
        first = await repository.upsert_batch([listing])
        again = await repository.upsert_batch([listing])
        raised = await repository.upsert_batch(
            [listing.model_copy(update={"price": Decimal("480.00"), "price_initial": None})]
        )
        await session.commit()
        history = (await session.scalars(select(ListingPriceHistory))).all()
        stored = await session.scalar(select(Listing))

    assert (first.inserted, first.price_changes) == (1, 0)
    assert (again.updated, again.price_changes) == (1, 0)
    assert raised.price_changes == 1
    assert [row.price for row in history] == [Decimal("450.00"), Decimal("480.00")]
    assert stored is not None
    # The first known starting price is kept; a later hit never rewrites it.
    assert stored.price_initial == Decimal("500.00")


async def test_a_listing_built_without_market_fields_starts_at_its_price(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "plain.db")
    plain = (await _normalize(ACTIVE_HIT)).model_copy(update={"price_initial": None})
    async with factory() as session:
        await ListingRepository(session).upsert_batch([plain])
        await session.commit()
        stored = await session.scalar(select(Listing))

    assert stored is not None and stored.price_initial == Decimal("450.00")


async def _legacy_rows(factory: async_sessionmaker[AsyncSession]) -> None:
    """Listings saved before the market columns existed, one with purged raw data."""

    hits: tuple[tuple[dict[str, Any], ListingStatus, datetime | None], ...] = (
        (ACTIVE_HIT, "active", None),
        (SOLD_HIT, "sold", None),
        ({**ACTIVE_HIT, "objectID": "503", "price_drops": [700]}, "active", None),
        ({**ACTIVE_HIT, "objectID": "504"}, "active", OBSERVED),
    )
    async with factory() as session:
        for payload, status, purged in hits:
            listing = await _normalize(payload, status=status)
            row = Listing(**listing.model_dump(exclude=set(MARKET_COLUMNS)))
            if purged is not None:
                row.raw_json = {}
                row.raw_json_purged_at = purged
            session.add(row)
        await session.commit()


async def test_the_backfill_fills_every_listing_in_committed_batches(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "backfill.db")
    await _legacy_rows(factory)
    async with factory() as session:
        commits: list[int] = []
        original = session.commit

        async def counting() -> None:
            commits.append(1)
            await original()

        session.commit = counting  # type: ignore[method-assign]
        result = await backfill_market_fields(session, load_source_mapping(), batch_size=3)
        again = await backfill_market_fields(session, load_source_mapping(), batch_size=3)
        rows = {row.grailed_id: row for row in (await session.scalars(select(Listing))).all()}

    assert result == {"status": "ok", "updated": 4, "from_raw_json": 3, "batches": 2}
    assert len(commits) == 2
    assert again["updated"] == 0
    assert all(row.price_initial is not None for row in rows.values())
    assert rows[502].price_initial == Decimal("600.00") and rows[502].seller_txn_count == 88
    assert rows[503].price_initial == Decimal("700.00") and rows[503].price_drop_count == 1
    # Purged raw data leaves only what the row itself knows.
    assert rows[504].price_initial == Decimal("450.00") and rows[504].price_drop_count is None


def _alembic(database: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    return config


def _listing_columns(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {row[1] for row in connection.execute("PRAGMA table_info(listings)")}


def test_the_migration_adds_and_drops_the_market_columns(tmp_path: Path) -> None:
    database = tmp_path / "market.db"
    config = _alembic(database)

    command.upgrade(config, "20261015_0021")
    command.upgrade(config, "head")
    added = _listing_columns(database)
    command.downgrade(config, "20261015_0021")
    dropped = _listing_columns(database)

    assert set(MARKET_COLUMNS) <= added
    assert not set(MARKET_COLUMNS) & dropped
