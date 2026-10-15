"""Wardrobe season presets: the suggestion, the config file and the API (plan 3.3)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import cli
from app.core.config import Settings
from app.db.models import Base, Brand, GroupMetric, SeasonProfile
from app.db.session import get_db
from app.main import app
from app.services.metrics.season_presets import (
    SEASONS_PATH,
    SeasonCandidate,
    load_season_presets,
    render_yaml,
    suggest_presets,
    wardrobe_types,
)

T0 = datetime(2026, 10, 15, 12, tzinfo=UTC)


def _seasons(**si_rel: float | None) -> dict[str, Any]:
    return {
        season: None if value is None else {"sales": 100.0, "si_abs": value, "si_rel": value}
        for season, value in {
            "autumn": None,
            "winter": None,
            "spring": None,
            "summer": None,
            **si_rel,
        }.items()
    }


def test_types_above_the_threshold_are_suggested_strongest_first() -> None:
    profiles = [
        ("coat", _seasons(autumn=1.29, winter=1.28)),
        ("parka", _seasons(autumn=1.86, winter=1.15)),
        ("tshirt", _seasons(autumn=1.149, summer=None)),
        ("review", _seasons(autumn=2.0)),
    ]

    suggestion = suggest_presets(profiles, ["coat", "parka", "tshirt"])

    assert [item.product_type for item in suggestion["autumn"]] == ["parka", "coat"]
    # The threshold is inclusive.
    assert [item.product_type for item in suggestion["winter"]] == ["coat", "parka"]
    assert suggestion["spring"] == [] and suggestion["summer"] == []


def test_the_suggestion_renders_as_a_seasons_block() -> None:
    suggestion = {
        "autumn": [SeasonCandidate("parka", 1.857, 77.0)],
        "winter": [],
        "spring": [],
        "summer": [SeasonCandidate("shorts", 1.25, 475.0)],
    }

    text = render_yaml(suggestion, 1.15)

    assert "    - parka  # SI_rel 1.86, 77 sales" in text
    assert yaml.safe_load(text) == {
        "seasons": {"autumn": ["parka"], "winter": [], "spring": [], "summer": ["shorts"]}
    }


def test_the_shipped_presets_list_known_wardrobe_types() -> None:
    presets = load_season_presets(SEASONS_PATH)

    assert set(presets) == {"autumn", "winter", "spring", "summer"}
    assert "parka" in presets["autumn"] and "shorts" in presets["summer"]
    assert "review" not in wardrobe_types()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("other: 1\n", "expected a 'seasons' mapping"),
        ("seasons:\n  monsoon: [parka]\n", "unknown season"),
        ("seasons:\n  autumn: parka\n", "must list type ids"),
        ("seasons:\n  autumn: [parka, kilt]\n", "unknown types ['kilt']"),
    ],
)
def test_a_broken_presets_file_is_rejected(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "seasons.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        load_season_presets(path)


def test_a_missing_presets_file_means_no_presets_and_duplicates_collapse(tmp_path: Path) -> None:
    path = tmp_path / "seasons.yaml"
    path.write_text("seasons:\n  winter: [coat, parka, coat]\n", encoding="utf-8")

    assert load_season_presets(tmp_path / "absent.yaml") == {}
    assert load_season_presets(path) == {"winter": ("coat", "parka")}


def _metric(key: str, product_type: str, sold_30d: int) -> GroupMetric:
    return GroupMetric(
        scope_key=key,
        scope="type",
        brand_id=1,
        product_type=product_type,
        section="outerwear",
        group_id=None,
        is_line=False,
        is_fallback=False,
        listings=50,
        sold_7d=0,
        sold_30d=sold_30d,
        sold_prev_30d=1,
        sold_90d=9,
        weekly_sales=[0] * 12,
        weekly_median_price=[None] * 12,
        median_price_30d=Decimal("900.00"),
        median_price_90d=Decimal("900.00"),
        active_now=20,
        new_listings_14d=1,
        sell_through_30d=Decimal("0.05"),
        growth=Decimal("1"),
        is_new=False,
        colors=[],
        sizes=[],
        computed_at=T0,
    )


def _profile(product_type: str, department: str | None, seasons: dict[str, Any]) -> SeasonProfile:
    return SeasonProfile(
        profile_key=f"type:{product_type}:{department or '*'}",
        kind="type",
        product_type=product_type,
        department=department,
        months=[],
        seasons=seasons,
        months_covered=11,
        sales_year=Decimal(1000),
        computed_at=T0,
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
        session.add_all(
            [
                _metric("type:1:parka", "parka", 5),
                _metric("type:1:coat", "coat", 3),
                _metric("type:1:tshirt", "tshirt", 9),
                _profile("parka", None, _seasons(autumn=1.86)),
                # A department's own profile is not the type's: never suggested.
                _profile("tshirt", "menswear", _seasons(autumn=1.5)),
            ]
        )
        await session.commit()
    await engine.dispose()
    return factory


def test_the_cli_prints_the_suggestion_from_the_database(tmp_path: Path) -> None:
    database = tmp_path / "presets.db"
    asyncio.run(_seed(database))
    settings = Settings(database_url=f"sqlite+aiosqlite:///{database}")

    text = asyncio.run(cli.seasons_suggest(settings, minimum=1.15))

    assert yaml.safe_load(text)["seasons"]["autumn"] == ["parka"]
    assert "tshirt" not in text


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    factory = asyncio.run(_seed(tmp_path / "api.db"))

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_taxonomy_carries_the_presets(client: TestClient) -> None:
    body = client.get("/api/grouping/taxonomy").json()

    assert body["seasons"]["winter"] == list(load_season_presets()["winter"])


def test_the_trends_list_takes_several_types(client: TestClient) -> None:
    response = client.get(
        "/api/trends", params={"level": "type", "product_type": "parka,coat", "sort": "sales"}
    )

    assert response.status_code == 200, response.text
    assert [row["product_type"] for row in response.json()["data"]] == ["parka", "coat"]
    assert client.get("/api/trends", params={"product_type": "Parka;"}).status_code == 422
