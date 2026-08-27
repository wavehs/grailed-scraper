"""Regression, persistence, and API contracts for stage-nine scoring."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.models import (
    Base,
    Brand,
    Listing,
    ListingModelAssignment,
    ListingPriceHistory,
    ParserRun,
    ParserRunTask,
    ScoringSnapshot,
)
from app.db.session import get_db
from app.main import app
from app.services.identity.service import IdentityResolver
from app.services.scoring.calculator import (
    confidence_score,
    engagement_score,
    frequency_score,
    ratio_score,
    velocity_score,
)
from app.services.scoring.service import OpportunityScoringService

AS_OF = datetime(2026, 8, 8, 12, tzinfo=UTC)


def test_decimal_formula_boundaries_are_absolute_and_frequency_capped() -> None:
    assert ratio_score(1, 4) == Decimal("25.00")
    assert ratio_score(0, 0) == Decimal("0")
    assert velocity_score(Decimal(0)) == Decimal("100.00")
    assert velocity_score(Decimal(30)) == Decimal("50.00")
    assert frequency_score(1, 30) == Decimal("25.00")
    assert frequency_score(3, 30) == Decimal("50.00")
    assert frequency_score(3, 90) == Decimal("25.00")
    assert engagement_score(Decimal(20)) == Decimal("50.00")
    assert confidence_score(
        sample=Decimal(100),
        coverage=Decimal(100),
        quality=Decimal(100),
        temporal=Decimal(100),
        degraded=True,
        truncated=True,
    ) == Decimal("69.00")


async def _database(
    tmp_path: Path,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage9.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


def _listing(
    *,
    listing_id: int,
    run_id: int,
    brand_id: int,
    title: str,
    category: str,
    status: str,
    price: str,
    created_days_ago: int,
    sold_days_ago: int | None = None,
    flags: list[str] | None = None,
    no_photos: bool = False,
    size: str = "M",
    color: str | None = "Black",
) -> Listing:
    sold_at = AS_OF - timedelta(days=sold_days_ago) if sold_days_ago is not None else None
    created_at = AS_OF - timedelta(days=created_days_ago)
    days = (sold_at - created_at).days if sold_at is not None else None
    return Listing(
        source="grailed",
        grailed_id=listing_id,
        status=status,
        url=f"https://example.test/{listing_id}",
        title=title,
        description=None,
        brand_name_raw="Rick Owens",
        brand_slug="rick-owens",
        brand_id=brand_id,
        category=category,
        subcategory=None,
        size_raw=size,
        size_normalized=size,
        condition_raw="Used",
        condition="used",
        color=color,
        price=Decimal(price),
        price_original=Decimal(price),
        currency_original="USD",
        fx_rate=Decimal(1),
        sold_price=Decimal(price) if status == "sold" else None,
        likes_count=20,
        created_at=created_at,
        sold_at=sold_at,
        sold_at_is_estimated=False,
        updated_at=AS_OF,
        first_seen_at=created_at,
        last_seen_at=AS_OF,
        removed_checked_at=None,
        days_on_market=days,
        cover_photo_url=None if no_photos else "https://images.test/1.jpg",
        photo_urls=[] if no_photos else ["https://images.test/1.jpg"],
        photo_count=0 if no_photos else 1,
        seller_identity=f"seller-{listing_id}",
        seller_identity_mode="hashed",
        seller_country="US",
        quality_flags=flags or (["no_photos"] if no_photos else []),
        fetch_tier="T1",
        parser_run_id=run_id,
        raw_json={"id": listing_id},
        schema_version=1,
    )


async def _seed(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int, int]:
    async with factory() as session:
        brand = Brand(
            name="Rick Owens",
            slug="rick-owens",
            aliases=[],
            include_subbrands=False,
            created_at=AS_OF,
            updated_at=AS_OF,
        )
        session.add(brand)
        await session.flush()
        run = ParserRun(
            source="grailed",
            mode="full",
            status="running",
            phase="scoring",
            dry_run=False,
            degraded_mode=False,
            tier_used="T1",
            requests_made=2,
            coverage_avg=Decimal(1),
            warnings=[],
            stats={},
            created_at=AS_OF,
            started_at=AS_OF,
        )
        session.add(run)
        await session.flush()
        session.add_all(
            [
                ParserRunTask(
                    run_id=run.id,
                    brand_id=brand.id,
                    index_type=index_type,
                    status="done",
                    attempts=1,
                    hits_collected=10,
                    coverage=Decimal(1),
                )
                for index_type in ("active", "sold")
            ]
        )
        session.add_all(
            [
                _listing(
                    listing_id=1001,
                    run_id=run.id,
                    brand_id=brand.id,
                    title="Archive leather jacket",
                    category="outerwear",
                    status="sold",
                    price="100.00",
                    created_days_ago=20,
                    sold_days_ago=5,
                    no_photos=True,
                    size="M",
                    color="Black",
                ),
                _listing(
                    listing_id=1002,
                    run_id=run.id,
                    brand_id=brand.id,
                    title="Archive leather jacket",
                    category="outerwear",
                    status="active",
                    price="120.00",
                    created_days_ago=2,
                    size="M",
                    color="Black",
                ),
                _listing(
                    listing_id=1003,
                    run_id=run.id,
                    brand_id=brand.id,
                    title="Archive tee",
                    category="tops",
                    status="sold",
                    price="80.00",
                    created_days_ago=15,
                    sold_days_ago=4,
                    size="S",
                    color=None,
                ),
                _listing(
                    listing_id=1004,
                    run_id=run.id,
                    brand_id=brand.id,
                    title="Geobasket boots",
                    category="footwear",
                    status="active",
                    price="300.00",
                    created_days_ago=3,
                    size="42",
                    color="Milk",
                ),
                _listing(
                    listing_id=1005,
                    run_id=run.id,
                    brand_id=brand.id,
                    title="Archive jacket replica",
                    category="outerwear",
                    status="sold",
                    price="10.00",
                    created_days_ago=10,
                    sold_days_ago=2,
                    flags=["possible_replica"],
                    size="L",
                    color="Red",
                ),
                _listing(
                    listing_id=1006,
                    run_id=run.id,
                    brand_id=brand.id,
                    title="Archive jacket repost",
                    category="outerwear",
                    status="active",
                    price="110.00",
                    created_days_ago=1,
                    flags=["repost"],
                    size="L",
                    color="Red",
                ),
                _listing(
                    listing_id=1007,
                    run_id=run.id,
                    brand_id=brand.id,
                    title="Archive leather jacket",
                    category="outerwear",
                    status="sold",
                    price="130.00",
                    created_days_ago=18,
                    sold_days_ago=3,
                    size="L",
                    color="Red",
                ),
                _listing(
                    listing_id=1008,
                    run_id=run.id,
                    brand_id=brand.id,
                    title="Archive leather jacket",
                    category="outerwear",
                    status="sold",
                    price="140.00",
                    created_days_ago=12,
                    sold_days_ago=1,
                    size="M",
                    color="Black",
                ),
                _listing(
                    listing_id=1009,
                    run_id=run.id,
                    brand_id=brand.id,
                    title="Geobasket boots",
                    category="footwear",
                    status="active",
                    price="310.00",
                    created_days_ago=60,
                    size="43",
                    color="Milk",
                ),
            ]
        )
        await session.flush()
        await IdentityResolver(session, get_settings()).resolve_run(run.id)
        specific_group_id = int(
            await session.scalar(
                select(ListingModelAssignment.model_group_id)
                .join(Listing, Listing.id == ListingModelAssignment.listing_id)
                .where(Listing.grailed_id == 1001)
            )
            or 0
        )
        await session.commit()
        return run.id, brand.id, specific_group_id


async def test_scoring_persists_two_idempotent_windows_and_quality_policy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = await _database(tmp_path)
    run_id, _, specific_group_id = await _seed(factory)
    service = OpportunityScoringService(factory)
    first = await service.score_run(run_id)
    second = await service.score_run(run_id)
    assert first == second
    assert first == {
        "status": "completed",
        "model_version": "market-v5",
        "windows": [30, 90],
        "groups": 3,
        "snapshots": 6,
    }
    async with factory() as session:
        snapshots = list(
            await session.scalars(
                select(ScoringSnapshot).order_by(
                    ScoringSnapshot.window_days, ScoringSnapshot.model_group_id
                )
            )
        )
        specific = next(
            item
            for item in snapshots
            if item.window_days == 30 and item.model_group_id == specific_group_id
        )
    assert len(snapshots) == 6
    extended = next(
        item
        for item in snapshots
        if item.window_days == 90 and item.model_group_id == specific_group_id
    )
    insufficient = next(
        item for item in snapshots if item.window_days == 30 and item.sold_count == 1
    )
    assert specific.sold_count == 3
    assert specific.exact_sold_count == 3
    assert specific.active_count == 2
    assert specific.quality_summary == {
        "candidates": 6,
        "usable": 5,
        "excluded": 1,
        "exact_sold": 3,
        "no_photos": 1,
        "price_excluded": 0,
    }
    assert specific.variant_breakdown["sizes"][:2] == [
        {"value": "m", "sold_count": 2, "active_count": 1, "sell_through": "0.666667"},
        {"value": "l", "sold_count": 1, "active_count": 1, "sell_through": "0.500000"},
    ]
    assert specific.confidence_score < 100
    assert specific.scoring_status == "scored"
    assert specific.demand_score is not None
    assert specific.liquidity_score is not None
    assert extended.active_count == specific.active_count
    assert extended.sold_count >= specific.sold_count
    assert extended.demand_score is not None and extended.demand_score <= Decimal("33.33")
    assert insufficient.scoring_status == "insufficient_sales"
    assert insufficient.demand_score is None
    assert insufficient.liquidity_score is None
    await engine.dispose()


async def test_scoring_can_replace_snapshots_inside_callers_transaction(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, factory = await _database(tmp_path)
    run_id, brand_id, _ = await _seed(factory)
    service = OpportunityScoringService(factory)
    await service.score_run(run_id)
    async with factory() as session:
        original = {
            row.id: row.input_digest
            for row in await session.scalars(
                select(ScoringSnapshot).where(ScoringSnapshot.parser_run_id == run_id)
            )
        }
        listing = await session.scalar(
            select(Listing).where(Listing.brand_id == brand_id).order_by(Listing.id).limit(1)
        )
        assert listing is not None
        listing.likes_count += 1
        result = await service.score_run_in_session(
            session,
            run_id,
            brand_ids={brand_id},
            replace=True,
        )
        changed = {
            row.id: row.input_digest
            for row in await session.scalars(
                select(ScoringSnapshot).where(ScoringSnapshot.parser_run_id == run_id)
            )
        }
        assert result["status"] == "completed"
        assert changed != original
        await session.rollback()
    async with factory() as session:
        persisted = {
            row.id: row.input_digest
            for row in await session.scalars(
                select(ScoringSnapshot).where(ScoringSnapshot.parser_run_id == run_id)
            )
        }
    assert persisted == original
    await engine.dispose()


def test_stage9_analytics_api_uses_exact_cents(tmp_path) -> None:  # type: ignore[no-untyped-def]
    async def scenario() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession], int, int, int]:
        engine, factory = await _database(tmp_path)
        run_id, brand_id, group_id = await _seed(factory)
        await OpportunityScoringService(factory).score_run(run_id)
        async with factory() as session:
            run = await session.get(ParserRun, run_id)
            assert run is not None
            run.status = "completed"
            listing_id = int(await session.scalar(select(func.min(Listing.id))) or 0)
            session.add(
                ListingPriceHistory(
                    listing_id=listing_id,
                    price=Decimal("99.99"),
                    observed_at=AS_OF,
                    source_run_id=run_id,
                )
            )
            await session.commit()
        return engine, factory, brand_id, group_id, listing_id

    engine, factory, brand_id, group_id, listing_id = asyncio.run(scenario())

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = get_settings
    try:
        client = TestClient(app)
        dashboard = client.get("/api/analytics/dashboard?window_days=30")
        sorted_dashboard = client.get(
            "/api/analytics/dashboard?window_days=30&sort_by=sold_count&sort_desc=false"
        )
        filtered_dashboard = client.get(
            f"/api/analytics/dashboard?window_days=30&brand_id={brand_id}"
            "&product_type=footwear&limit=1"
        )
        detail = client.get(f"/api/analytics/model-groups/{group_id}?window_days=30")
        brands = client.get("/api/analytics/brands?window_days=30")
        brand = client.get(f"/api/analytics/brands/{brand_id}?window_days=30")
        listing = client.get(f"/api/analytics/listings/{listing_id}")
        catalog = client.get("/api/analytics/listings")
        catalog_first = client.get("/api/analytics/listings?limit=1")
        catalog_cursor = catalog_first.json()["next_cursor"]

        async def touch_last_seen() -> None:
            async with factory() as session:
                newest = await session.scalar(select(Listing).order_by(Listing.id.desc()))
                assert newest is not None
                newest.last_seen_at = AS_OF + timedelta(days=1)
                await session.commit()

        asyncio.run(touch_last_seen())
        catalog_second = client.get(
            "/api/analytics/listings", params={"limit": 1, "cursor": catalog_cursor}
        )
        invalid_catalog_cursor = client.get(
            "/api/analytics/listings",
            params={"limit": 1, "status": "sold", "cursor": catalog_cursor},
        )
        dashboard_first = client.get("/api/analytics/dashboard?window_days=30&limit=1")
        dashboard_second = client.get(
            "/api/analytics/dashboard",
            params={
                "window_days": 30,
                "limit": 1,
                "cursor": dashboard_first.json()["next_cursor"],
            },
        )
        history = client.get(f"/api/analytics/listings/{listing_id}/price-history")
        removed_rules = client.get("/api/model-rules")
        removed_identity = client.get("/api/identity/candidates")
    finally:
        app.dependency_overrides.clear()
    assert dashboard.status_code == 200, dashboard.text
    assert detail.status_code == 200, detail.text
    assert len(dashboard.json()["data"]) == 3
    sold_counts = [item["sold_count"] for item in sorted_dashboard.json()["data"]]
    assert sold_counts == sorted(sold_counts)
    assert filtered_dashboard.json()["next_cursor"] is None
    assert filtered_dashboard.json()["data"][0]["category"] == "footwear"
    assert isinstance(detail.json()["metrics"]["median_sold_price"], int)
    variants = detail.json()["variant_breakdown"]
    assert (
        sum(row["sold_count"] for row in variants["sizes"])
        == detail.json()["metrics"]["sold_count"]
    )
    assert (
        sum(row["active_count"] for row in variants["sizes"])
        == detail.json()["metrics"]["active_count"]
    )
    assert brands.json()["next_cursor"] is None
    assert brands.json()["data"][0]["groups_count"] == 3
    assert brands.json()["data"][0]["sold_count"] == 4
    assert brands.json()["data"][0]["exact_sold_count"] == 4
    assert brands.json()["data"][0]["active_count"] == 4
    assert brands.json()["data"][0]["demand_score"] is not None
    assert isinstance(brands.json()["data"][0]["median_sold_price"], int)
    assert brand.status_code == listing.status_code == history.status_code == 200
    assert isinstance(listing.json()["price"], int)
    assert catalog.status_code == 200
    assert catalog.json()["data"]
    assert isinstance(catalog.json()["data"][0]["price"], int)
    assert catalog.json()["data"][0]["days_on_market"] is not None
    assert catalog_first.json()["data"][0]["id"] != catalog_second.json()["data"][0]["id"]
    assert invalid_catalog_cursor.status_code == 422
    assert dashboard_first.json()["data"][0]["id"] != dashboard_second.json()["data"][0]["id"]
    assert (
        dashboard_first.json()["data"][0]["run_id"] == dashboard_second.json()["data"][0]["run_id"]
    )
    assert history.json()["data"][0]["price"] == 9999
    assert removed_rules.status_code == removed_identity.status_code == 404
    asyncio.run(engine.dispose())


async def test_stage9_model_exposes_tables_constraints_and_indexes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine, _ = await _database(tmp_path)
    async with engine.begin() as connection:
        schema = await connection.run_sync(
            lambda sync: {
                "tables": set(inspect(sync).get_table_names()),
                "snapshot_indexes": {
                    item["name"] for item in inspect(sync).get_indexes("scoring_snapshots")
                },
            }
        )
    assert {"model_groups", "scoring_snapshots"} <= cast(set[str], schema["tables"])
    assert "model_rules" not in cast(set[str], schema["tables"])
    assert {
        "ix_scoring_snapshots_group_window_run",
        "ix_scoring_snapshots_brand_window_opportunity",
        "ix_scoring_snapshots_brand_window_demand",
    } <= cast(set[str], schema["snapshot_indexes"])
    await engine.dispose()
