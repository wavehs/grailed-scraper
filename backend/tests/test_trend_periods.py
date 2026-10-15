"""Month periods of the trends list: sales from ``group_month_stats`` and coverage (plan 3.2)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.errors import ApiError
from app.api.trend_periods import MonthRange, month_coverage, parse_period
from app.db.models import Base, Brand, GroupMetric, GroupMonthStat, Listing, ModelGroup
from app.db.session import get_db
from app.main import app

T0 = datetime(2026, 10, 15, 12, tzinfo=UTC)
AUTUMN_2025 = MonthRange(date(2025, 9, 1), date(2025, 11, 1))


def test_a_period_needs_both_ends_in_order_and_two_years_at_most() -> None:
    assert parse_period(None, None) is None
    assert parse_period("2025-12", "2026-02") == MonthRange(date(2025, 12, 1), date(2026, 2, 1))
    for month_from, month_to in (("2025-09", None), ("2026-02", "2025-12"), ("2023-01", "2025-01")):
        with pytest.raises(ApiError) as error:
            parse_period(month_from, month_to)
        assert error.value.code == "invalid_period"


def test_coverage_names_missing_and_partial_months() -> None:
    coverage = month_coverage(AUTUMN_2025, date(2025, 10, 9), date(2026, 10, 15))

    assert coverage.missing == ["2025-09"]
    assert [(item.month, item.from_day, item.to_day) for item in coverage.partial] == [
        ("2025-10", 9, 31)
    ]


def test_the_running_month_is_partial_and_later_months_missing() -> None:
    period = MonthRange(date(2026, 9, 1), date(2026, 11, 1))

    coverage = month_coverage(period, date(2025, 10, 9), date(2026, 10, 15))

    assert coverage.missing == ["2026-11"]
    assert [(item.month, item.to_day) for item in coverage.partial] == [("2026-10", 15)]


def test_without_data_every_month_is_missing() -> None:
    coverage = month_coverage(AUTUMN_2025, None, None)

    assert coverage.missing == ["2025-09", "2025-10", "2025-11"] and coverage.partial == []


def _metric(key: str, scope: str, group_id: int | None, **values: Any) -> GroupMetric:
    base: dict[str, Any] = {
        "scope_key": key,
        "scope": scope,
        "brand_id": 1,
        "product_type": "parka",
        "section": "outerwear",
        "group_id": group_id,
        "is_line": scope == "model",
        "is_fallback": False,
        "listings": 50,
        "sold_7d": 0,
        "sold_30d": 1,
        "sold_prev_30d": 1,
        "sold_90d": 9,
        "weekly_sales": [0] * 12,
        "weekly_median_price": [None] * 12,
        "median_price_30d": Decimal("900.00"),
        "median_price_90d": Decimal("900.00"),
        "active_now": 20,
        "new_listings_14d": 1,
        "sell_through_30d": Decimal("0.05"),
        "growth": Decimal("1"),
        "is_new": False,
        "colors": [],
        "sizes": [],
        "computed_at": T0,
    }
    return GroupMetric(**{**base, **values})


def _month(key: str, month: date, bucket: str, sold: int, p50: str | None) -> GroupMonthStat:
    return GroupMonthStat(
        scope_key=key,
        scope=key.split(":")[0],
        brand_id=1,
        month=month,
        cond_bucket=bucket,
        sold_count=sold,
        gmv=Decimal(sold) * Decimal(p50 or 0),
        p50=Decimal(p50) if p50 else None,
        computed_at=T0,
    )


def _sold_listing(listing_id: int, sold_at: datetime) -> Listing:
    return Listing(
        id=listing_id,
        grailed_id=listing_id,
        status="sold",
        url=f"https://www.grailed.com/listings/{listing_id}",
        title="Parka",
        brand_name_raw="Rick Owens",
        brand_id=1,
        product_type="parka",
        price=Decimal(900),
        sold_price=Decimal(850),
        currency_original="USD",
        created_at=sold_at,
        sold_at=sold_at,
        first_seen_at=sold_at,
        last_seen_at=T0,
        photo_urls=[],
        seller_identity_mode="none",
        quality_flags=[],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
    )


async def _seed(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'periods.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    group = {
        "brand_id": 1,
        "product_type": "parka",
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
                ModelGroup(id=1, slug="mountain", name="Mountain", **group),
                ModelGroup(id=2, slug="sisyphus", name="Sisyphus", **group),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _metric("type:1:parka", "type", None),
                _metric("model:1", "model", 1, sold_30d=5),
                _metric("model:2", "model", 2, sold_30d=1),
                _month("model:1", date(2025, 10, 1), "all", 2, "1000.00"),
                _month("model:1", date(2025, 11, 1), "all", 1, "700.00"),
                _month("model:1", date(2025, 11, 1), "used", 1, "700.00"),
                # Outside autumn 2025: never counted.
                _month("model:1", date(2026, 1, 1), "all", 9, "100.00"),
                _month("model:2", date(2025, 11, 1), "all", 6, "1200.00"),
                _month("model:2", date(2025, 11, 1), "new", 6, "1200.00"),
                _month("type:1:parka", date(2025, 11, 1), "all", 7, "1100.00"),
                _sold_listing(1, datetime(2025, 10, 9, 17, tzinfo=UTC)),
            ]
        )
        await session.commit()
    return factory


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    factory = asyncio.run(_seed(tmp_path))

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _rows(client: TestClient, **params: str) -> dict[str, Any]:
    response = client.get("/api/trends", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_season_counts_its_months_and_weights_monthly_medians(client: TestClient) -> None:
    body = _rows(client, month_from="2025-09", month_to="2025-11", sort="sales")

    rows = {row["name"]: row for row in body["data"]}
    assert [row["name"] for row in body["data"]] == ["Sisyphus", "Mountain"]
    assert rows["Mountain"]["sold"] == 3
    # (2 × 1000 + 1 × 700) / 3 = 900.
    assert rows["Mountain"]["median_price"] == 90_000
    assert rows["Sisyphus"]["sold"] == 6
    coverage = body["period"]
    assert coverage["missing"] == ["2025-09"]
    assert coverage["partial"][0] == {"month": "2025-10", "from_day": 9, "to_day": 31}
    assert coverage["data_from"] == "2025-10-09"


def test_a_period_condition_keeps_scopes_with_sales_in_it(client: TestClient) -> None:
    body = _rows(client, month_from="2025-09", month_to="2025-11", condition="used")

    assert [(row["name"], row["sold"]) for row in body["data"]] == [("Mountain", 1)]


def test_the_period_sales_drive_filters_and_the_no_model_share(client: TestClient) -> None:
    body = _rows(client, month_from="2025-11", month_to="2025-11", min_sales="2")

    assert [row["name"] for row in body["data"]] == ["Sisyphus"]
    assert body["no_model"]["total_sold"] == 7


def test_without_a_period_the_window_stays(client: TestClient) -> None:
    body = _rows(client, sort="sales")

    assert body["period"] is None
    assert [row["sold"] for row in body["data"]] == [5, 1]


def test_half_a_period_is_rejected(client: TestClient) -> None:
    response = client.get("/api/trends", params={"month_from": "2025-09"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_period"
