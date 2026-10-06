"""Group metrics formulas, persistence and the trends, card and catalog APIs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import Base, Brand, GroupMetric, Listing, ListingPriceHistory
from app.db.session import get_db
from app.main import app
from app.services.grouping import GroupingService
from app.services.metrics import MetricsService
from app.services.metrics.calculator import MetricListing, compute_metrics, trend_score

AS_OF = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _item(
    item_id: int,
    *,
    status: str = "sold",
    price: str = "100",
    sold_days: float | None = None,
    listed_days: float = 40,
    created_days: float | None = None,
    exact: bool = True,
    usable: bool = True,
    relist: bool = False,
    color: str | None = "Black",
    size: str | None = "M",
) -> MetricListing:
    listed = AS_OF - timedelta(days=listed_days)
    return MetricListing(
        id=item_id,
        status=status,
        price=Decimal(price),
        sold_price=Decimal(price) if status == "sold" else None,
        item_created_at=AS_OF - timedelta(days=created_days) if created_days else listed,
        listed_at=listed,
        sold_at=AS_OF - timedelta(days=sold_days) if sold_days is not None else None,
        exact_sale=exact,
        price_usable=usable,
        is_relist=relist,
        color=color,
        size=size,
    )


def test_windows_series_medians_and_trend_formula() -> None:
    listings = [
        _item(1, price="100", sold_days=1, listed_days=11),
        _item(2, price="200", sold_days=5, listed_days=25),
        _item(3, price="300", sold_days=20, listed_days=30),
        _item(4, price="900", sold_days=25, listed_days=30, usable=False),
        _item(5, price="150", sold_days=40, listed_days=60),
        _item(6, price="120", sold_days=80, listed_days=100, exact=False),
        _item(7, status="active", price="250", listed_days=3, color="Grey", size="L"),
        _item(8, status="active", price="260", listed_days=20, color=None),
        # A relist: time to sell starts at the first listing of the item.
        _item(9, price="210", sold_days=2, listed_days=4, created_days=62, relist=True),
    ]
    metrics = compute_metrics(listings, AS_OF)
    assert (metrics.sold_7d, metrics.sold_30d, metrics.sold_prev_30d, metrics.sold_90d) == (
        3,
        5,
        1,
        7,
    )
    assert sum(metrics.weekly_sales) == 7 and metrics.weekly_sales[-1] == 3
    assert metrics.median_price_30d == Decimal("205.00")  # 100, 200, 210, 300; 900 excluded
    assert metrics.median_price_90d == Decimal("175.00")
    assert metrics.price_change == Decimal("0.1714")
    assert metrics.median_days_to_sell == Decimal("10.00")  # 10, 20, 10, 5, 60 days
    assert metrics.active_now == 2 and metrics.new_listings_14d == 2
    assert metrics.sell_through_30d == Decimal("0.714286")
    assert metrics.growth == Decimal("3.0000")  # (5 + 1) / (1 + 1)
    assert metrics.speed == Decimal("0.7500")  # 30 / (30 + 10)
    assert metrics.trend_score == Decimal("160.71")
    assert metrics.colors[0] == {
        "value": "black",
        "sold": 7,
        "active": 0,
        "sell_through": "1.000000",
    }
    assert {"value": "gray", "sold": 0, "active": 1, "sell_through": "0.000000"} in metrics.colors
    assert not metrics.is_new  # the oldest listing is 100 days old


def test_trend_needs_three_sales_and_smooths_growth() -> None:
    assert trend_score(2, Decimal(3), Decimal(1), Decimal(1)) is None
    assert trend_score(3, Decimal(4), None, Decimal("0.5")) == Decimal("100.00")
    single = compute_metrics([_item(1, sold_days=3, listed_days=10)], AS_OF)
    assert single.growth == Decimal("2.0000") and single.trend_score is None
    assert single.is_new


def _listing(
    listing_id: int,
    brand_id: int,
    title: str,
    path: str,
    *,
    status: str,
    price: str,
    days_ago: int,
    seller: str,
    sold_days: int | None = None,
) -> Listing:
    created = AS_OF - timedelta(days=days_ago)
    return Listing(
        source="grailed",
        grailed_id=listing_id,
        status=status,
        url=f"https://www.grailed.com/listings/{listing_id}",
        title=title,
        brand_name_raw="Brand",
        brand_id=brand_id,
        category=path.partition(".")[0],
        category_path=path,
        price=Decimal(price),
        currency_original="USD",
        sold_price=Decimal(price) if status == "sold" else None,
        created_at=created,
        sold_at=AS_OF - timedelta(days=sold_days) if sold_days is not None else None,
        first_seen_at=created,
        last_seen_at=AS_OF,
        size_normalized="M",
        color="Black",
        photo_urls=[],
        designer_names=[],
        seller_identity=seller,
        seller_identity_mode="hashed",
        quality_flags=[],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
    )


async def _seed(tmp_path: Path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        brands = ((1, "Balenciaga", "balenciaga"), (2, "Rick Owens", "rick-owens"))
        for brand_id, name, slug in brands:
            session.add(
                Brand(
                    id=brand_id,
                    name=name,
                    slug=slug,
                    aliases=[],
                    include_subbrands=False,
                    created_at=AS_OF,
                    updated_at=AS_OF,
                )
            )
        rows = []
        for index in range(6):
            rows.append(
                _listing(
                    100 + index,
                    1,
                    "Balenciaga Track 2 Sneakers" if index % 2 else "Balenciaga Track Sneakers",
                    "footwear.lowtop_sneakers",
                    status="sold",
                    price=str(500 + index * 10),
                    days_ago=20 + index,
                    sold_days=index + 1,
                    seller=f"s{index}",
                )
            )
        rows.append(
            _listing(
                120, 1, "Balenciaga Track", "footwear.lowtop_sneakers",
                status="active", price="700", days_ago=3, seller="s9",
            )
        )
        rows.append(
            _listing(
                130, 1, "Balenciaga Speedhunters Hoodie", "tops.sweatshirts_hoodies",
                status="sold", price="300", days_ago=10, sold_days=2, seller="s20",
            )
        )
        for index in range(4):
            rows.append(
                _listing(
                    200 + index,
                    2,
                    "Rick Owens Geobasket",
                    "footwear.hitop_sneakers",
                    status="sold",
                    price="800",
                    days_ago=50,
                    sold_days=index * 10 + 1,
                    seller=f"r{index}",
                )
            )
        session.add_all(rows)
        await session.commit()
        await GroupingService(session).regroup()
        await session.commit()
        await MetricsService(session).recompute(as_of=AS_OF)
        session.add(
            ListingPriceHistory(listing_id=1, price=Decimal("99.99"), observed_at=AS_OF)
        )
        await session.commit()
    return engine, factory


def test_trends_show_every_brand_and_the_group_card(tmp_path: Path) -> None:
    engine, factory = asyncio.run(_seed(tmp_path))

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def track_line() -> int:
        async with factory() as session:
            metric = await session.scalar(
                select(GroupMetric).where(GroupMetric.scope == "model", GroupMetric.is_line)
                .order_by(GroupMetric.sold_30d.desc())
            )
            assert metric is not None and metric.group_id is not None
            return metric.group_id

    line_id = asyncio.run(track_line())
    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        models = client.get("/api/trends").json()
        footwear = client.get(
            "/api/trends", params={"section": "footwear", "sort": "sales", "window": 90}
        ).json()
        filtered = client.get(
            "/api/trends", params={"brand_ids": "2", "min_sales": 3, "price_min": 700}
        ).json()
        cheap = client.get("/api/trends", params={"price_max": 100}).json()
        types = client.get("/api/trends", params={"level": "type"}).json()
        brands = client.get("/api/trends", params={"level": "brand", "sort": "growth"}).json()
        card = client.get(f"/api/trends/groups/{line_id}").json()
        catalog = client.get("/api/listings", params={"product_type": "hoodie"}).json()
        first = client.get("/api/listings", params={"limit": 2}).json()
        second = client.get(
            "/api/listings", params={"limit": 2, "cursor": first["next_cursor"]}
        ).json()
        history = client.get("/api/listings/1/price-history").json()
        missing = client.get("/api/trends/groups/99999")
        removed = [client.get(path).status_code for path in (
            "/api/analytics/dashboard", "/api/analytics/model-groups/1", "/api/identity/candidates"
        )]
    finally:
        app.dependency_overrides.clear()

    names = {(row["brand"], row["name"]) for row in models["data"]}
    assert {("Balenciaga", "Track"), ("Rick Owens", "Geobasket")} <= names
    assert all(row["scope"] == "model" and not row["is_fallback"] for row in models["data"])
    track = next(row for row in models["data"] if row["name"] == "Track")
    assert track["versions"] == 1 and track["sold_30d"] == 6 and track["active_now"] == 1
    assert track["trend_score"] is not None and len(track["weekly_sales"]) == 12
    assert isinstance(track["median_price"], int)
    assert [row["section"] for row in footwear["data"]] == ["footwear"] * len(footwear["data"])
    assert footwear["data"][0]["name"] == "Track"
    assert [row["name"] for row in filtered["data"]] == ["Geobasket"]
    assert cheap["data"] == []
    assert {row["product_type"] for row in types["data"]} >= {"lowtop_sneakers", "hoodie"}
    assert {row["name"] for row in brands["data"]} == {"Balenciaga", "Rick Owens"}
    assert card["group"]["name"] == "Track" and card["metrics"]["sold_30d"] == 6
    assert [row["name"] for row in card["versions"]] == ["Track 2"]
    assert card["versions"][0]["sold_30d"] == 3
    assert card["colors"][0]["value"] == "black" and card["sizes"][0]["value"] == "m"
    assert card["recent_sales"][0]["url"].startswith("https://www.grailed.com/listings/")
    assert card["recent_sales"][0]["days_to_sell"] is not None
    assert len(card["active_listings"]) == 1
    assert card["type_metrics"]["product_type"] == "lowtop_sneakers"
    assert len(card["weekly_median_price"]) == 12
    assert [row["title"] for row in catalog["data"]] == ["Balenciaga Speedhunters Hoodie"]
    assert catalog["data"][0]["product_type"] == "hoodie"
    assert first["data"][0]["id"] != second["data"][0]["id"]
    assert history["data"][0]["price"] == 9999
    assert missing.status_code == 404
    assert removed == [404, 404, 404]
    asyncio.run(engine.dispose())
