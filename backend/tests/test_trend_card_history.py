"""The model card's year, season overlay, size P30 and fee schedule (plan 3.4)."""

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

from app.api.trend_card_history import card_months, month_points, type_overlay
from app.db.models import Base, Brand, GroupMetric, GroupMonthStat, ModelGroup, SeasonProfile
from app.db.session import get_db
from app.main import app

T0 = datetime(2026, 10, 15, 12, tzinfo=UTC)
TODAY = datetime.now(UTC).date()
SIZES = [
    {
        "value": "42",
        "sold": 12,
        "active": 5,
        "sell_through": "0.7",
        "p30": "0.61",
        "p30_n": 14,
        "hot": True,
    },
    {"value": "45", "sold": 2, "active": 9, "sell_through": "0.18"},
]


def test_the_card_year_ends_with_the_running_month() -> None:
    months = card_months(date(2026, 3, 9))

    assert len(months) == 12
    assert months[0] == date(2025, 4, 1) and months[-1] == date(2026, 3, 1)


def _metric(**values: Any) -> GroupMetric:
    base: dict[str, Any] = {
        "scope_key": "model:7",
        "scope": "model",
        "brand_id": 1,
        "product_type": "parka",
        "section": "outerwear",
        "group_id": 7,
        "is_line": True,
        "is_fallback": False,
        "listings": 50,
        "sold_7d": 0,
        "sold_30d": 1,
        "sold_prev_30d": 1,
        "sold_90d": 9,
        "weekly_sales": [0] * 12,
        "weekly_median_price": [None] * 12,
        "median_price_30d": Decimal("900.00"),
        "active_now": 20,
        "new_listings_14d": 1,
        "sell_through_30d": Decimal("0.05"),
        "growth": Decimal("1"),
        "is_new": False,
        "colors": [],
        "sizes": SIZES,
        "department": "menswear",
        "computed_at": T0,
    }
    return GroupMetric(**{**base, **values})


def _month(month: date, bucket: str, sold: int, p50: str | None) -> GroupMonthStat:
    return GroupMonthStat(
        scope_key="model:7",
        scope="model",
        brand_id=1,
        group_id=7,
        month=month,
        cond_bucket=bucket,
        sold_count=sold,
        gmv=Decimal(sold) * Decimal(p50 or 0),
        p50=Decimal(p50) if p50 else None,
        computed_at=T0,
    )


def _profile(key: str, january: float) -> SeasonProfile:
    return SeasonProfile(
        profile_key=key,
        kind="type",
        product_type="parka",
        department=key.rsplit(":", 1)[1].replace("*", "") or None,
        months=[{"month": 1, "sales": 30.0, "si_abs": january, "si_rel": january}],
        seasons={},
        months_covered=11,
        sales_year=Decimal(300),
        stable=None,
        computed_at=T0,
    )


async def _seed(path: Path, *, menswear_profile: bool = True) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    running = TODAY.replace(day=1)
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
        session.add(
            ModelGroup(
                id=7,
                brand_id=1,
                product_type="parka",
                slug="sisyphus",
                name="Sisyphus",
                aliases=[],
                status="confirmed",
                source="seed",
                created_at=T0,
                updated_at=T0,
            )
        )
        await session.flush()
        profiles = [_profile("type:parka:*", 1.4)]
        if menswear_profile:
            profiles.append(_profile("type:parka:menswear", 1.7))
        session.add_all(
            [
                _metric(),
                _month(running, "all", 3, "900.00"),
                _month(running, "new", 1, None),
                _month(running, "used", 2, "850.00"),
                # Older than the card's year: never shown.
                _month(date(running.year - 2, 1, 1), "all", 9, "500.00"),
                *profiles,
            ]
        )
        await session.commit()
    await engine.dispose()
    return factory


async def test_months_fill_gaps_with_zero_and_flag_the_running_one(tmp_path: Path) -> None:
    factory = await _seed(tmp_path / "months.db")
    async with factory() as session:
        points = await month_points(session, "model:7", TODAY)

    assert len(points) == 12 and sum(point.sold for point in points) == 3
    last = points[-1]
    assert last.partial and last.month == TODAY.strftime("%Y-%m")
    assert (last.sold_new, last.p50_new, last.sold_used, last.p50_used) == (1, None, 2, 85_000)
    assert points[0].sold == 0 and points[0].p50 is None and not points[0].partial


async def test_the_overlay_prefers_the_department_profile(tmp_path: Path) -> None:
    factory = await _seed(tmp_path / "overlay.db", menswear_profile=False)
    async with factory() as session:
        fallback = await type_overlay(session, "parka", "menswear")
        missing = await type_overlay(session, "coat", None)
        untyped = await type_overlay(session, None, None)

    assert fallback is not None and fallback.profile_key == "type:parka:*"
    assert fallback.si_abs[0] == pytest.approx(1.4) and fallback.si_abs[1:] == [None] * 11
    assert missing is None and untyped is None


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    factory = asyncio.run(_seed(tmp_path / "card.db"))

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_card_carries_the_year_the_overlay_and_size_p30(client: TestClient) -> None:
    response = client.get("/api/trends/groups/7")

    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert len(body["months"]) == 12 and body["months"][-1]["sold"] == 3
    assert body["season_overlay"]["profile_key"] == "type:parka:menswear"
    assert body["season_overlay"]["si_abs"][0] == pytest.approx(1.7)
    hot, plain = body["sizes"]
    assert (hot["p30"], hot["p30_n"], hot["hot"]) == ("0.61", 14, True)
    assert (plain["p30"], plain["p30_n"], plain["hot"]) == (None, None, False)


def test_the_fee_schedule_is_served_as_decimal_strings(client: TestClient) -> None:
    body: dict[str, Any] = client.get("/api/fees").json()

    assert body["effective_from"] == "2026-05-20"
    assert body["commission"]["tiers"][1] == {"from_usd": "120", "rate": "0.09"}
    assert body["processing"]["fixed_usd"] == "0.49"
