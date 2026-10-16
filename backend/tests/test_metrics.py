"""Group metrics formulas, persistence and the trends, card and catalog APIs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import (
    AppSetting,
    Base,
    Brand,
    GroupMetric,
    Listing,
    ListingPriceHistory,
)
from app.db.session import get_db
from app.main import app
from app.services.grouping import GroupingService
from app.services.metrics import MetricsService
from app.services.metrics.calculator import MetricListing, compute_metrics
from app.services.metrics.liquidity_store import POLICY_KEY

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
    likes: int = 0,
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
        likes=likes,
    )


def test_windows_series_and_medians() -> None:
    listings = [
        _item(1, price="100", sold_days=1, listed_days=11, likes=10),
        _item(2, price="200", sold_days=5, listed_days=25, likes=40),
        _item(3, price="300", sold_days=20, listed_days=30, likes=25),
        _item(4, price="900", sold_days=25, listed_days=30, usable=False, likes=7),
        _item(5, price="150", sold_days=40, listed_days=60, likes=30),
        _item(6, price="120", sold_days=80, listed_days=100, exact=False, likes=12),
        # Active likes are not demand that turned into a sale.
        _item(7, status="active", price="250", listed_days=3, color="Grey", size="L", likes=500),
        _item(8, status="active", price="260", listed_days=20, color=None),
        # A relist: time to sell starts at the first listing of the item.
        _item(9, price="210", sold_days=2, listed_days=4, created_days=62, relist=True, likes=19),
        # Older than 90 days: only in the 180 and 365 day periods.
        _item(10, price="210", sold_days=120, listed_days=130, likes=900),
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
    # Every rankable period: sales, median price (usable), days to sell (exact), likes.
    assert metrics.windows["7"] == {"sold": 3, "price": "200.00", "days": "20.00", "likes": "19.0"}
    assert metrics.windows["14"] == metrics.windows["7"]
    assert metrics.windows["30"] == {"sold": 5, "price": "205.00", "days": "10.00", "likes": "19.0"}
    assert metrics.windows["60"] == {"sold": 6, "price": "200.00", "days": "15.00", "likes": "22.0"}
    assert metrics.windows["90"] == {"sold": 7, "price": "175.00", "days": "15.00", "likes": "19.0"}
    year = {"sold": 8, "price": "200.00", "days": "10.00", "likes": "22.0"}
    assert metrics.windows["180"] == year and metrics.windows["365"] == year
    assert metrics.colors[0] == {
        "value": "black",
        "sold": 7,
        "active": 0,
        "sell_through": "1.000000",
    }
    assert {"value": "gray", "sold": 0, "active": 1, "sell_through": "0.000000"} in metrics.colors
    assert not metrics.is_new  # the oldest listing is 100 days old


def test_periods_without_sales_are_left_out() -> None:
    single = compute_metrics([_item(1, sold_days=10, listed_days=12, likes=3)], AS_OF)
    unsold = compute_metrics([_item(1, status="active", likes=50)], AS_OF)

    assert set(single.windows) == {"14", "30", "60", "90", "180", "365"} and single.is_new
    assert single.windows["14"]["likes"] == "3.0"
    assert unsold.windows == {}


_TYPICAL = ("100", "105", "95", "110", "90", "100", "102", "98")


def test_a_far_sale_price_is_left_out_of_medians_once_a_scope_has_eight_sales() -> None:
    sales = [_item(index, price=price, sold_days=index) for index, price in enumerate(_TYPICAL)]
    far = [_item(20, price="2000", sold_days=3), _item(21, price="5", sold_days=4)]

    metrics = compute_metrics([*sales, *far], AS_OF)

    assert metrics.sold_7d == 10 and metrics.sold_90d == 10  # still counted as sales
    assert metrics.median_price_90d == Decimal("100.00")
    assert metrics.median_price_7d == Decimal("100.00")
    assert metrics.weekly_median_price[-1] == Decimal("100.00")


def test_a_scope_with_fewer_than_eight_recent_sales_keeps_every_price() -> None:
    recent = [_item(index, price=price, sold_days=10) for index, price in enumerate(_TYPICAL[:6])]
    # Sales older than 90 days neither count towards the eight nor move the median.
    old = [_item(10 + index, price="100", sold_days=120) for index in range(5)]
    far = _item(20, price="2000", sold_days=3)

    metrics = compute_metrics([*recent, *old, far], AS_OF)

    assert metrics.median_price_90d == Decimal("100.00")  # 7 prices, 2000 among them
    assert metrics.median_price_7d == Decimal("2000.00")


def test_tied_prices_do_not_make_ordinary_sales_outliers() -> None:
    # Half the sales at one price leave a tiny MAD; a 1.3x sale is still ordinary.
    prices = ("100", "100", "100", "100", "105", "110", "120", "130")
    sales = [_item(index, price=price, sold_days=index) for index, price in enumerate(prices)]

    metrics = compute_metrics(sales, AS_OF)

    assert metrics.median_price_90d == Decimal("102.50")


def test_equal_prices_leave_out_only_a_sale_more_than_three_times_off() -> None:
    # The base sales are older than a week, so the 7-day median shows the one recent sale.
    sales = [_item(index, price="100", sold_days=30 + index) for index in range(8)]

    close = compute_metrics([*sales, _item(20, price="290", sold_days=2)], AS_OF)
    far = compute_metrics([*sales, _item(20, price="310", sold_days=2)], AS_OF)

    assert close.median_price_7d == Decimal("290.00")
    assert far.median_price_7d is None and far.sold_7d == 1


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
    likes: int = 0,
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
        likes_count=likes,
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
                    likes=10 + index,
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
        everything = client.get("/api/trends", params={"include_unranked": "true"}).json()
        footwear = client.get(
            "/api/trends", params={"section": "footwear", "sort": "sales"}
        ).json()
        filtered = client.get(
            "/api/trends",
            params={
                "brand_ids": "2", "min_sales": 3, "price_min": 700, "include_unranked": "true"
            },
        ).json()
        cheap = client.get("/api/trends", params={"price_max": 100}).json()
        types = client.get(
            "/api/trends", params={"level": "type", "include_unranked": "true"}
        ).json()
        brands = client.get(
            "/api/trends", params={"level": "brand", "sort": "likes", "include_unranked": "true"}
        ).json()
        old_sort = client.get("/api/trends", params={"sort": "trend"})
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

    # Geobasket sold 3 times in 30 days: below the threshold, so only Track is ranked.
    assert [(row["brand"], row["name"]) for row in models["data"]] == [("Balenciaga", "Track")]
    assert models["ranking"] == {
        "period_days": 30,
        "min_sales_per_month": "5",
        "min_price": 15000,
        "excluded_sections": ["dresses", "other"],
        "weights": {"sales": "0.40", "speed": "0.25", "likes": "0.20", "price": "0.15"},
    }
    names = [row["name"] for row in everything["data"]]
    assert names[0] == "Track" and "Geobasket" in names
    geobasket = next(row for row in everything["data"] if row["name"] == "Geobasket")
    assert geobasket["liquidity_score"] is None
    assert all(row["scope"] == "model" and not row["is_fallback"] for row in models["data"])
    track = models["data"][0]
    assert track["versions"] == 1 and track["sold_30d"] == 6 and track["active_now"] == 1
    assert track["liquidity_score"] == "50.00"  # alone in the pool
    assert track["period_median_likes"] == "12.5" and track["period_median_days"] == "19.00"
    assert track["period_sold"] == 6 and track["sales_per_month"] == "6.0"
    assert track["period_median_price"] == track["median_price"]
    assert "median_likes" not in track
    assert len(track["weekly_sales"]) == 12 and isinstance(track["median_price"], int)
    assert "trend_score" not in track
    assert old_sort.status_code == 422
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


def test_ranking_settings_are_saved_and_re_rank_at_once(tmp_path: Path) -> None:
    engine, factory = asyncio.run(_seed(tmp_path))

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    quarter = {
        "period_days": 90,
        "min_sales_per_month": 1,
        "min_price_usd": 150,
        "excluded_sections": ["dresses"],
        "weights": {"sales": 60, "speed": 20, "likes": 20, "price": 0},
    }
    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        defaults = client.get("/api/settings/liquidity").json()
        saved = client.put("/api/settings/liquidity", json=quarter).json()
        ranked = client.get("/api/trends").json()
        reread = client.get("/api/settings/liquidity").json()
        bad_period = client.put("/api/settings/liquidity", json={**quarter, "period_days": 45})
        bad_section = client.put(
            "/api/settings/liquidity", json={**quarter, "excluded_sections": ["gowns"]}
        )
        no_weight = client.put(
            "/api/settings/liquidity",
            json={**quarter, "weights": {"sales": 0, "speed": 0, "likes": 0, "price": 0}},
        )
        reset = client.delete("/api/settings/liquidity").json()
        after_reset = client.get("/api/trends").json()
    finally:
        app.dependency_overrides.clear()

    assert defaults["saved"] is False and defaults["period_days"] == 30
    assert defaults["periods"] == [7, 14, 30, 60, 90, 180, 365]
    assert defaults["defaults"]["min_price_usd"] == 150
    assert saved["saved"] is True and saved["period_days"] == 90 and saved["ranked"] >= 2
    assert saved["weights"] == {
        "sales": "0.6000", "speed": "0.2000", "likes": "0.2000", "price": "0.0000"
    }
    assert reread["min_sales_per_month"] == "1" and reread["excluded_sections"] == ["dresses"]
    # Over 90 days with one sale a month Geobasket passes too.
    assert {row["name"] for row in ranked["data"]} >= {"Track", "Geobasket"}
    assert ranked["ranking"]["period_days"] == 90
    assert ranked["ranking"]["weights"]["sales"] == "0.60"
    geobasket = next(row for row in ranked["data"] if row["name"] == "Geobasket")
    assert geobasket["period_sold"] >= 3 and geobasket["liquidity_score"] is not None
    assert [bad_period.status_code, bad_section.status_code, no_weight.status_code] == [422] * 3
    assert bad_section.json()["error"]["code"] == "invalid_liquidity_policy"
    assert reset["saved"] is False and reset["period_days"] == 30
    assert [row["name"] for row in after_reset["data"]] == ["Track"]
    asyncio.run(engine.dispose())


def test_a_broken_saved_policy_falls_back_to_the_defaults(tmp_path: Path) -> None:
    engine, factory = asyncio.run(_seed(tmp_path))

    async def break_policy() -> None:
        async with factory() as session:
            session.add(
                AppSetting(
                    key=POLICY_KEY,
                    value={"period_days": 30, "excluded_sections": ["gowns"]},
                    updated_at=AS_OF,
                )
            )
            await session.commit()

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    asyncio.run(break_policy())
    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        settings = client.get("/api/settings/liquidity").json()
        trends = client.get("/api/trends").json()
        huge = client.put(
            "/api/settings/liquidity",
            json={
                "period_days": 30,
                "min_sales_per_month": 5,
                "min_price_usd": 150,
                "excluded_sections": [],
                "weights": {name: "9E+999999" for name in ("sales", "speed", "likes", "price")},
            },
        )
        reset = client.delete("/api/settings/liquidity")
        reset_again = client.delete("/api/settings/liquidity")
    finally:
        app.dependency_overrides.clear()

    assert settings["saved"] is False and settings["period_days"] == 30
    assert [row["name"] for row in trends["data"]] == ["Track"]
    assert huge.status_code == 422
    assert reset.status_code == 200 and reset_again.status_code == 200
    assert reset_again.json()["saved"] is False and reset_again.json()["ranked"] >= 1
    asyncio.run(engine.dispose())


def test_rows_without_windows_stay_unranked(tmp_path: Path) -> None:
    engine, factory = asyncio.run(_seed(tmp_path))

    async def rank_after_migration() -> tuple[int, int]:
        async with factory() as session:
            # Right after migration 0021 no row has windows yet.
            await session.execute(update(GroupMetric).values(windows=None))
            ranked = await MetricsService(session).rank()
            await session.commit()
            empty = await session.scalar(
                select(func.count()).where(GroupMetric.period_sold == 0)
            )
            return ranked, int(empty or 0)

    ranked, empty = asyncio.run(rank_after_migration())

    assert ranked == 0 and empty > 0
    asyncio.run(engine.dispose())
