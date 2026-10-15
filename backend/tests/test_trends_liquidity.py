"""Liquidity columns, condition prices and the new filters of the trends table (plan 3.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.trend_fields import price_bucket
from app.db.models import Base, Brand, GroupMetric, ModelGroup
from app.db.session import get_db
from app.main import app

T0 = datetime(2026, 10, 15, tzinfo=UTC)


def _bucket(**values: Any) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "n": 0,
        "gmv": "0.00",
        "p25": None,
        "p50": None,
        "p75": None,
        "offer_discount": None,
        "total_markdown": None,
        "days_at_final_price": None,
        "fresh_n": 0,
        "fresh_ask_p50": None,
        "fresh_ask_premium": None,
    }
    return {**empty, **values}


TYPE_PRICING = {
    "all": _bucket(
        n=40,
        gmv="16000.00",
        p25="300.00",
        p50="400.00",
        p75="520.00",
        offer_discount="0.0800",
        total_markdown="0.2100",
    ),
    "new": _bucket(n=10, gmv="5000.00", p25="450.00", p50="500.00", p75="560.00"),
    "used": _bucket(n=30, gmv="11000.00", p25="290.00", p50="360.00", p75="430.00"),
}
GEO_PRICING = {
    "all": _bucket(
        n=12,
        gmv="6000.00",
        p25="420.00",
        p50="500.00",
        p75="610.00",
        offer_discount="0.0500",
        fresh_n=8,
        fresh_ask_p50="650.00",
        fresh_ask_premium="0.3000",
    ),
    "new": _bucket(n=3, gmv="1800.00", fresh_n=5, fresh_ask_p50="700.00"),
    "used": _bucket(n=9, gmv="4200.00", p25="400.00", p50="460.00", p75="520.00"),
}


def _metric(key: str, scope: str, **values: Any) -> GroupMetric:
    base: dict[str, Any] = {
        "scope_key": key,
        "scope": scope,
        "brand_id": 1,
        "product_type": "hightop_sneakers",
        "section": "footwear",
        "group_id": None,
        "is_line": scope == "model",
        "is_fallback": False,
        "listings": 100,
        "sold_7d": 1,
        "sold_30d": 4,
        "sold_prev_30d": 3,
        "sold_90d": 12,
        "weekly_sales": [1] * 12,
        "weekly_median_price": [None] * 12,
        "median_price_30d": Decimal("500.00"),
        "median_price_90d": Decimal("500.00"),
        "active_now": 60,
        "new_listings_14d": 2,
        "sell_through_30d": Decimal("0.06"),
        "growth": Decimal("1.2"),
        "is_new": False,
        "colors": [],
        "sizes": [],
        "computed_at": T0,
        "department": "menswear",
    }
    return GroupMetric(**{**base, **values})


async def _seed(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trends.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    group = {
        "brand_id": 1,
        "product_type": "hightop_sneakers",
        "aliases": [],
        "status": "confirmed",
        "source": "seed",
        "created_at": T0,
        "updated_at": T0,
    }
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
        session.add_all(
            [
                ModelGroup(id=1, slug="geobasket", name="Geobasket", **group),
                ModelGroup(id=2, slug="ramones", name="Ramones", **group),
                ModelGroup(id=3, slug="kiss", name="Kiss", **group),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _metric(
                    "type:1:hightop_sneakers",
                    "type",
                    listings=900,
                    sold_90d=40,
                    pricing=TYPE_PRICING,
                ),
                _metric(
                    "model:1",
                    "model",
                    group_id=1,
                    liquidity_score=Decimal("81.50"),
                    p30=Decimal("0.300000"),
                    p30_sold=6,
                    p30_cohort=20,
                    active_effective=30,
                    active_fresh=12,
                    gmv_90d=Decimal("6000.00"),
                    pricing=GEO_PRICING,
                ),
                _metric(
                    "model:2",
                    "model",
                    group_id=2,
                    sold_90d=30,
                    liquidity_score=Decimal("64.00"),
                    p30=Decimal("0.450000"),
                    p30_sold=9,
                    p30_cohort=20,
                    department="womenswear",
                    pricing={
                        "all": _bucket(n=30, p50="300.00"),
                        "new": _bucket(),
                        "used": _bucket(n=30, p50="300.00"),
                    },
                ),
                _metric(
                    "model:3",
                    "model",
                    group_id=3,
                    sold_90d=2,
                    liquidity_score=Decimal("12.00"),
                    p30=Decimal("0.100000"),
                    p30_sold=0,
                    p30_cohort=0,
                    pricing=None,
                ),
            ]
        )
        await session.commit()
    return factory


def _client(factory: async_sessionmaker[AsyncSession]) -> TestClient:
    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _names(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [row["name"] for row in response.json()["data"]]


@pytest.fixture
def client(tmp_path: Path) -> Any:
    import asyncio

    factory = asyncio.run(_seed(tmp_path))
    try:
        yield _client(factory)
    finally:
        app.dependency_overrides.clear()


def test_rows_carry_liquidity_columns(client: TestClient) -> None:
    rows = {row["name"]: row for row in client.get("/api/trends").json()["data"]}

    geo = rows["Geobasket"]
    assert Decimal(geo["liquidity_score"]) == Decimal("81.50")
    assert Decimal(geo["p30"]) == Decimal("0.3")
    assert Decimal(geo["p30_raw"]) == Decimal("0.3") and geo["p30_n"] == 20
    assert Decimal(geo["expected_days"]) == Decimal("84.11")
    assert geo["active_effective"] == 30 and geo["active_fresh"] == 12
    assert geo["gmv_90d"] == 600_000 and geo["department"] == "menswear"
    assert geo["price"]["p50"] == 50_000 and geo["price"]["approximate"] is False
    assert Decimal(geo["price"]["fresh_ask_premium"]) == Decimal("0.3")
    # No cohort: no raw rate, and a missing pricing block gives no price.
    assert rows["Kiss"]["p30_raw"] is None and rows["Kiss"]["price"] is None


def test_a_bucket_without_medians_falls_back_to_the_type(client: TestClient) -> None:
    rows = client.get("/api/trends", params={"condition": "new"}).json()["data"]
    geo = next(row for row in rows if row["name"] == "Geobasket")

    price = geo["price"]
    assert price["approximate"] is True
    assert (price["p25"], price["p50"], price["p75"]) == (45_000, 50_000, 56_000)
    # Own sales and fresh asks stay; the premium is against the parent's median.
    assert price["n"] == 3 and price["fresh_ask_p50"] == 70_000
    assert Decimal(price["fresh_ask_premium"]) == Decimal("0.4")


def test_condition_department_and_low_data_filters(client: TestClient) -> None:
    assert _names(client.get("/api/trends", params={"condition": "new"})) == ["Geobasket"]
    assert _names(client.get("/api/trends", params={"department": "womenswear"})) == ["Ramones"]
    assert "Kiss" not in _names(client.get("/api/trends", params={"hide_low_data": True}))


def test_sorting_by_liquidity_and_p30(client: TestClient) -> None:
    by_score = _names(client.get("/api/trends", params={"sort": "liquidity"}))
    by_p30 = _names(client.get("/api/trends", params={"sort": "p30"}))
    ascending = _names(client.get("/api/trends", params={"sort": "liquidity", "desc": False}))

    assert by_score == ["Geobasket", "Ramones", "Kiss"]
    assert by_p30 == ["Ramones", "Geobasket", "Kiss"]
    assert ascending == ["Kiss", "Ramones", "Geobasket"]


def test_an_unknown_condition_is_rejected(client: TestClient) -> None:
    assert client.get("/api/trends", params={"condition": "mint"}).status_code == 422


def test_price_bucket_without_any_data_is_none() -> None:
    assert price_bucket(None, "all", None) is None
    assert price_bucket({"all": _bucket()}, "used", None) is None
