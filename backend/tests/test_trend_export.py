"""CSV export of the trends list: same filters, BOM, formula-safe cells (v2 plan 3.6)."""

from __future__ import annotations

import asyncio
import csv
import io
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import trend_export
from app.api.trend_export import BOM, cell
from app.db.models import Base, Brand, GroupMetric
from app.db.session import get_db
from app.main import app

T0 = datetime(2026, 10, 15, 12, tzinfo=UTC)


def _metric(key: str, product_type: str, sold_30d: int, **extra: Any) -> GroupMetric:
    values: dict[str, Any] = {
        "scope_key": key,
        "scope": "type",
        "brand_id": 1,
        "product_type": product_type,
        "section": "outerwear",
        "group_id": None,
        "is_line": False,
        "is_fallback": False,
        "listings": 50,
        "sold_7d": 0,
        "sold_30d": sold_30d,
        "sold_prev_30d": 1,
        "sold_90d": 9,
        "weekly_sales": [0] * 12,
        "weekly_median_price": [None] * 12,
        "median_price_30d": Decimal("900.50"),
        "median_price_90d": Decimal("900.50"),
        "active_now": 20,
        "new_listings_14d": 1,
        "sell_through_30d": Decimal("0.05"),
        "growth": Decimal("1"),
        "is_new": False,
        "colors": [],
        "sizes": [],
        "computed_at": T0,
        **extra,
    }
    return GroupMetric(**values)


async def _seed(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Brand(
                id=1,
                name="=HYPERLINK(1)",
                slug="evil",
                aliases=[],
                include_subbrands=False,
                created_at=T0,
                updated_at=T0,
            )
        )
        await session.flush()
        session.add_all(
            [
                _metric("type:1:parka", "parka", 5, badges=["hot", "stable"], is_new=True),
                _metric("type:1:coat", "coat", 3),
                _metric("type:1:tshirt", "tshirt", 9),
            ]
        )
        await session.commit()
    await engine.dispose()
    return factory


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    factory = asyncio.run(_seed(tmp_path / "export.db"))

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _table(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text.removeprefix(BOM))))


def test_the_export_has_the_lists_rows_in_its_order(client: TestClient) -> None:
    params = {
        "level": "type",
        "product_type": "parka,coat",
        "sort": "sales",
        "include_unranked": "true",
    }

    response = client.get("/api/trends/export.csv", params=params)
    listed = client.get("/api/trends", params=params).json()["data"]

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="trends-type-' in response.headers["content-disposition"]
    assert response.content.startswith(BOM.encode("utf-8"))
    table = _table(response.text)
    assert [row["product_type"] for row in table] == [row["product_type"] for row in listed]
    assert [row["product_type"] for row in table] == ["parka", "coat"]


def test_cells_are_dollars_flags_and_formula_safe_text(client: TestClient) -> None:
    response = client.get(
        "/api/trends/export.csv",
        params={"level": "type", "sort": "sales", "include_unranked": "true"},
    )

    parka = next(row for row in _table(response.text) if row["product_type"] == "parka")
    assert parka["brand"] == "'=HYPERLINK(1)"
    assert parka["median_price_usd"] == "900.50"
    assert parka["badges"] == "hot stable"
    assert (parka["is_new"], parka["sold"], parka["liquidity_score"]) == ("1", "5", "")


def test_the_export_validates_like_the_list(client: TestClient) -> None:
    assert client.get("/api/trends/export.csv", params={"window": 14}).status_code == 422
    assert client.get("/api/trends/export.csv", params={"product_type": "x;"}).status_code == 422


def test_rows_stream_in_chunks(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trend_export, "CHUNK_ROWS", 1)
    monkeypatch.setattr(trend_export, "LOOKUP_BATCH", 2)

    with client.stream(
        "GET", "/api/trends/export.csv", params={"level": "type", "include_unranked": "true"}
    ) as response:
        chunks = list(response.iter_text())

    assert len(_table("".join(chunks))) == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (True, "1"),
        (False, "0"),
        (Decimal("-0.25"), "-0.25"),
        ("-Track", "'-Track"),
        ("@cmd", "'@cmd"),
        ("Geobasket", "Geobasket"),
    ],
)
def test_a_cell_is_rendered_safely(value: object, expected: str) -> None:
    assert cell(value) == expected
