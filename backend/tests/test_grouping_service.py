"""grouping-v6 persistence: assignments, delta passes, mining and manual edit rules."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import (
    Base,
    Brand,
    Listing,
    ListingModelAssignment,
    ModelGroup,
)
from app.db.session import get_db
from app.main import app
from app.services.grouping import GroupingService

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _listing(
    listing_id: int, title: str, path: str, seller: str, *, status: str = "active"
) -> Listing:
    created = NOW - timedelta(days=listing_id % 40 + 1)
    return Listing(
        source="grailed",
        grailed_id=listing_id,
        status=status,
        url=f"https://www.grailed.com/listings/{listing_id}",
        title=title,
        brand_name_raw="Chrome Hearts",
        brand_id=1,
        category=path.partition(".")[0],
        subcategory=path,
        category_path=path,
        price=Decimal("100.00"),
        currency_original="USD",
        sold_price=Decimal("100.00") if status == "sold" else None,
        created_at=created,
        sold_at=NOW - timedelta(days=1) if status == "sold" else None,
        first_seen_at=created,
        last_seen_at=NOW,
        photo_urls=[],
        designer_names=["Chrome Hearts"],
        seller_identity=seller,
        seller_identity_mode="hashed",
        quality_flags=[],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
    )


async def _database(tmp_path: Path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'grouping.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            Brand(
                id=1,
                name="Chrome Hearts",
                slug="chrome-hearts",
                aliases=["CH"],
                include_subbrands=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        rows = [
            _listing(1, "Chrome Hearts Dagger Pendant", "accessories.jewelry_watches", "s1"),
            _listing(2, "CH Dagger", "accessories.jewelry_watches", "s2", status="sold"),
            _listing(3, "Chrome Hearts Dagger Trucker Hat", "accessories.hats", "s3"),
            _listing(4, "Chrome Hearts Dagger Pendant", "accessories.hats", "s4"),
            _listing(5, "Chrome Hearts Baby Fat Cross Pendant", "womens_jewelry.necklaces", "s5"),
            _listing(
                6, "Chrome Hearts Horseshoe Hoodie Black XL", "tops.sweatshirts_hoodies", "s6"
            ),
        ]
        rows += [
            _listing(10 + index, f"Chrome Hearts Neck Logo Tee {size}", "tops.short_sleeve_shirts",
                     f"tee-{index}")
            for index, size in enumerate(["S", "M", "L", "XL", "M", "L"])
        ]
        rows += [
            _listing(30 + index, "Chrome Hearts Spam Phrase Tee", "tops.short_sleeve_shirts",
                     "spammer")
            for index in range(8)
        ]
        session.add_all(rows)
        await session.commit()
    return engine, factory


async def _groups_by_listing(session: AsyncSession) -> dict[int, ModelGroup]:
    rows = await session.execute(
        select(Listing.grailed_id, ModelGroup)
        .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
        .join(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
    )
    return {grailed_id: group for grailed_id, group in rows}


async def test_regroup_assigns_types_models_versions_and_mined_groups(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path)
    async with factory() as session:
        result = await GroupingService(session).regroup()
        await session.commit()
        groups = await _groups_by_listing(session)
        mixed = await session.scalar(
            select(func.count())
            .select_from(Listing)
            .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .join(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
            .where(Listing.product_type != ModelGroup.product_type)
        )
        type_rows = await session.execute(select(Listing.grailed_id, Listing.product_type))
        types = {grailed_id: product_type for grailed_id, product_type in type_rows}
    summary = result.summary()
    assert summary["listings"] == 20 and mixed == 0
    assert (groups[1].product_type, groups[1].name) == ("pendant", "Dagger")
    assert groups[2].id == groups[1].id  # no type words: the seeded Dagger infers "pendant"
    assert (groups[3].product_type, groups[3].slug) == ("hat", "_none")
    assert types[4] == "review"  # a pendant listed under hats is not put into a hat group
    assert groups[5].name == "Baby Fat" and groups[5].parent_id is not None
    assert (groups[6].product_type, groups[6].name) == ("hoodie", "Horseshoe")
    mined = groups[10]
    assert (mined.name, mined.status, mined.source) == ("Neck Logo", "auto", "mined")
    assert all(groups[10 + index].id == mined.id for index in range(6))
    assert groups[30].slug == "_none"  # eight listings of one seller are not a model

    async with factory() as session:
        again = await GroupingService(session).regroup()
        await session.commit()
    assert again.brands[0].full is False
    assert again.brands[0].processed == 0 and again.brands[0].changed == 0
    await engine.dispose()


def test_manual_edits_survive_regrouping(tmp_path: Path) -> None:
    engine, factory = asyncio.run(_database(tmp_path))

    async def prepare() -> dict[int, ModelGroup]:
        async with factory() as session:
            await GroupingService(session).regroup()
            await session.commit()
            return await _groups_by_listing(session)

    groups = asyncio.run(prepare())

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        taxonomy = client.get("/api/grouping/taxonomy").json()
        mined = groups[10]
        renamed = client.patch(f"/api/groups/{mined.id}", json={"name": "Neck Logo Tee"})
        duplicate = client.post(
            f"/api/groups/{groups[1].id}/split", json={"phrase": "dagger pendant xl"}
        )
        split = client.post(f"/api/groups/{groups[1].id}/split", json={"phrase": "Dagger Cross"})
        moved = client.put("/api/listings/3/group", json={"group_id": groups[1].id})
        foreign = client.put("/api/listings/3/group", json={"group_id": 999})
        not_model = client.post(f"/api/groups/{groups[6].id}/not-model")
        service = client.patch(f"/api/groups/{groups[3].id}", json={"name": "X"})
        listing = client.get("/api/groups", params={"brand_id": 1, "lines_only": True})
    finally:
        app.dependency_overrides.clear()

    assert {item["id"] for item in taxonomy["sections"]} >= {"tops", "jewelry"}
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Neck Logo Tee" and renamed.json()["status"] == "confirmed"
    assert "Neck Logo" in renamed.json()["aliases"]
    assert duplicate.status_code == 409  # "pendant xl" are type and size words: just "dagger"
    assert split.status_code == 201 and split.json()["parent_id"] == groups[1].id
    assert moved.status_code == 200 and moved.json()["id"] == groups[1].id
    assert foreign.status_code == 404
    assert not_model.status_code == 200 and not_model.json()["status"] == "ignored"
    assert service.status_code == 409
    assert all(item["status"] != "ignored" for item in listing.json()["data"])

    async def verify() -> dict[int, ModelGroup]:
        async with factory() as session:
            await GroupingService(session).regroup(full=True)
            await session.commit()
            return await _groups_by_listing(session)

    after = asyncio.run(verify())
    assert after[3].id == groups[1].id  # the manual move is a rule, not a one-off write
    assert after[10].name == "Neck Logo Tee"
    assert after[6].slug == "_none"  # "Horseshoe" is now a stopword for this brand
    asyncio.run(engine.dispose())
