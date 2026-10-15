"""Watchlist: follow models and see how they moved since (v2 plan 3.7)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import watchlist
from app.api.watchlist import WatchPoint, changes
from app.db.models import Base, Brand, GroupMetric, GroupMetricDaily, ModelGroup, WatchlistItem
from app.db.session import get_db
from app.main import app

BACKEND = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
TODAY = datetime.now(UTC).date()


def _group(group_id: int, name: str) -> ModelGroup:
    return ModelGroup(
        id=group_id,
        brand_id=1,
        product_type="parka",
        slug=name.lower(),
        name=name,
        aliases=[],
        status="confirmed",
        source="seed",
        created_at=T0,
        updated_at=T0,
    )


def _metric(group_id: int, score: str, price: str, active: int) -> GroupMetric:
    values: dict[str, Any] = {
        "scope_key": f"model:{group_id}",
        "scope": "model",
        "brand_id": 1,
        "product_type": "parka",
        "section": "outerwear",
        "group_id": group_id,
        "is_line": True,
        "is_fallback": False,
        "listings": 50,
        "sold_7d": 1,
        "sold_30d": 12,
        "sold_prev_30d": 8,
        "sold_90d": 30,
        "weekly_sales": [0] * 12,
        "weekly_median_price": [None] * 12,
        "median_price_30d": Decimal(price),
        "active_now": active,
        "active_effective": active - 5,
        "new_listings_14d": 1,
        "sell_through_30d": Decimal("0.2"),
        "growth": Decimal("1"),
        "is_new": False,
        "colors": [],
        "sizes": [],
        "computed_at": T0,
        "liquidity_score": Decimal(score),
    }
    return GroupMetric(**values)


def _snapshot(day: date, score: str, price: str, active: int) -> GroupMetricDaily:
    return GroupMetricDaily(
        snapshot_date=day,
        scope_key="model:7",
        scope="model",
        brand_id=1,
        group_id=7,
        sold_30d=10,
        sold_90d=25,
        active_now=active,
        active_effective=active - 5,
        median_price_30d=Decimal(price),
        sell_through_30d=Decimal("0.2"),
        liquidity_score=Decimal(score),
    )


async def _seed(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Brand(
                id=1,
                name="Rick Owens",
                slug="rick-owens",
                aliases=[],
                include_subbrands=False,
                created_at=T0,
                updated_at=T0,
            )
        )
        await session.flush()
        session.add_all([_group(7, "Sisyphus"), _group(8, "Bauhaus")])
        await session.flush()
        session.add_all(
            [
                _metric(7, "60", "1000.00", 30),
                _metric(8, "20", "500.00", 10),
                _snapshot(TODAY - timedelta(days=10), "40", "800.00", 20),
                _snapshot(TODAY - timedelta(days=3), "50", "900.00", 25),
            ]
        )
        await session.commit()
    await engine.dispose()
    return factory


@pytest.fixture
def factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    return asyncio.run(_seed(tmp_path / "watch.db"))


@pytest.fixture
def client(factory: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_watching_measures_changes_from_the_last_snapshot_before_adding(
    client: TestClient,
) -> None:
    response = client.put("/api/watchlist/7", json={"note": "  size 50  "})

    assert response.status_code == 200, response.text
    item: dict[str, Any] = response.json()
    assert (item["brand"], item["name"], item["note"], item["retired"]) == (
        "Rick Owens",
        "Sisyphus",
        "size 50",
        False,
    )
    assert item["baseline"]["as_of"] == str(TODAY - timedelta(days=3))
    assert item["changes"] == {
        "liquidity_score": "10.00",
        "median_price": "0.1111",
        "sold_30d": 2,
        "active_now": 5,
        "active_effective": 5,
    }


def test_watching_again_keeps_the_date_and_replaces_the_note(client: TestClient) -> None:
    first = client.put("/api/watchlist/7", json={"note": "a"}).json()

    again = client.put("/api/watchlist/7", json={"note": "   "}).json()
    listed = client.get("/api/watchlist").json()["data"]

    assert again["added_at"] == first["added_at"]
    assert again["note"] is None
    assert [row["group_id"] for row in listed] == [7]


def test_a_model_without_history_has_no_changes(client: TestClient) -> None:
    item = client.put("/api/watchlist/8", json={}).json()

    assert item["baseline"] is None and item["changes"] is None
    assert item["current"]["median_price"] == 50_000


def test_a_model_added_before_its_history_starts_at_the_first_snapshot(
    client: TestClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    async def add_early() -> None:
        async with factory() as session:
            added = datetime.combine(TODAY - timedelta(days=30), datetime.min.time(), UTC)
            session.add(WatchlistItem(group_id=7, added_at=added, note=None))
            await session.commit()

    asyncio.run(add_early())

    item = client.get("/api/watchlist").json()["data"][0]

    assert item["baseline"]["as_of"] == str(TODAY - timedelta(days=10))
    assert item["changes"]["liquidity_score"] == "20.00"


def test_the_list_gives_each_model_its_own_baseline(client: TestClient) -> None:
    client.put("/api/watchlist/7", json={})
    client.put("/api/watchlist/8", json={})

    listed = {row["group_id"]: row for row in client.get("/api/watchlist").json()["data"]}

    assert listed[7]["baseline"]["as_of"] == str(TODAY - timedelta(days=3))
    assert listed[8]["baseline"] is None
    assert listed[8]["current"]["liquidity_score"] == "20.00"


def test_a_note_edit_never_brings_back_a_removed_model(client: TestClient) -> None:
    client.put("/api/watchlist/7", json={"note": "a"})

    edited = client.patch("/api/watchlist/7", json={"note": " b "})
    client.delete("/api/watchlist/7")
    late = client.patch("/api/watchlist/7", json={"note": "c"})

    assert edited.status_code == 200 and edited.json()["note"] == "b"
    assert late.status_code == 404 and late.json()["error"]["code"] == "not_watched"
    assert client.get("/api/watchlist").json()["data"] == []


def test_unwatching_is_idempotent(client: TestClient) -> None:
    client.put("/api/watchlist/7", json={})

    assert client.delete("/api/watchlist/7").status_code == 204
    assert client.delete("/api/watchlist/7").status_code == 204
    assert client.get("/api/watchlist").json()["data"] == []


def test_bad_requests_are_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = client.put("/api/watchlist/999", json={})
    long_note = client.put("/api/watchlist/7", json={"note": "x" * 501})
    monkeypatch.setattr(watchlist, "WATCHLIST_MAX", 1)
    client.put("/api/watchlist/7", json={})
    full = client.put("/api/watchlist/8", json={})

    assert missing.status_code == 404 and missing.json()["error"]["code"] == "group_not_found"
    assert long_note.status_code == 422
    assert full.status_code == 409 and full.json()["error"]["code"] == "watchlist_full"


def test_the_trends_list_can_show_only_watched_models(client: TestClient) -> None:
    client.put("/api/watchlist/8", json={})

    every = client.get("/api/trends").json()["data"]
    watched = client.get("/api/trends", params={"watched_only": "true"}).json()["data"]

    assert {row["group_id"] for row in every} == {7, 8}
    assert [row["group_id"] for row in watched] == [8]


def _point(**overrides: Any) -> WatchPoint:
    values: dict[str, Any] = {
        "as_of": TODAY,
        "liquidity_score": Decimal(10),
        "median_price": 10_000,
        "sold_30d": 5,
        "active_now": 10,
        "active_effective": 8,
        **overrides,
    }
    return WatchPoint(**values)


def test_changes_need_both_sides_and_a_positive_old_price() -> None:
    result = changes(
        _point(median_price=0, active_effective=None), _point(liquidity_score=None, sold_30d=7)
    )

    assert (result.liquidity_score, result.median_price, result.active_effective) == (
        None,
        None,
        None,
    )
    assert (result.sold_30d, result.active_now) == (2, 0)


def test_the_migration_creates_and_drops_the_watchlist(tmp_path: Path) -> None:
    database = tmp_path / "migrate.db"
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database.as_posix()}"

    def tables() -> set[str]:
        with sqlite3.connect(database) as connection:
            rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            return {row[0] for row in rows}

    command.upgrade(config, "head")
    created = tables()
    command.downgrade(config, "20261015_0026")

    assert "watchlist" in created
    assert "watchlist" not in tables()
