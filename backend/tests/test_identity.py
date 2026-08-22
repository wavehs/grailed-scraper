"""Source-independent identity contracts; live parser acceptance remains separate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import (
    Base,
    Brand,
    IdentityMatch,
    Listing,
    ListingModelAssignment,
    ModelGroup,
    ParserRun,
    ParserRunTask,
    PhysicalItemMember,
)
from app.services.identity.images import fingerprint_bytes, hamming_distance
from app.services.identity.service import IdentityResolver, model_signature, model_text


def test_model_text_and_image_hash_are_stable() -> None:
    assert (
        model_text("Rick Owens RARE Geobasket Size 42 Dust", "Rick Owens", "42", "Dust")
        == "geobasket"
    )
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "black").save(buffer, format="PNG")
    first = fingerprint_bytes(buffer.getvalue())
    second = fingerprint_bytes(buffer.getvalue())
    assert first == second
    assert hamming_distance(first.dhash, second.dhash) == 0
    assert model_text(
        "Chrome Hearts Osaka Pocket T White Size XXL",
        "Chrome Hearts",
        "XXL",
        "White",
    ) == model_text(
        "Chrome Hearts Osaka Pocket T White XL QS",
        "Chrome Hearts",
        "XL",
        "White",
    )
    assert model_text("Osaka Pocket Tee SLT", "Chrome Hearts") == model_text(
        "Osaka Pocket T BK", "Chrome Hearts"
    )
    dagger = model_signature(
        "Chrome Hearts Dagger Pendant Cuban Link Chain Necklace 925",
        "Chrome Hearts",
        "One Size",
        "Silver",
        "accessories",
    )
    assert dagger == model_signature(
        "Dagger Necklace", "Chrome Hearts", "One Size", "Silver", "accessories"
    ) == ("necklace", "dagger")
    assert model_signature(
        "Chrome Hearts Double Dagger Pendant",
        "Chrome Hearts",
        category="accessories",
    ) != dagger
    assert model_signature(
        "Chrome Hearts Dagger Dog Tag Necklace",
        "Chrome Hearts",
        category="accessories",
    ) != dagger


async def test_resolver_groups_model_variants_and_same_seller_relist(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 15, tzinfo=UTC)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        brand = Brand(
            name="Rick Owens",
            slug="rick-owens",
            aliases=[],
            include_subbrands=False,
            created_at=now,
            updated_at=now,
        )
        session.add(brand)
        await session.flush()
        run = ParserRun(
            source="grailed",
            mode="full",
            status="running",
            phase="resolving_identity",
            dry_run=False,
            degraded_mode=False,
            requests_made=1,
            warnings=[],
            stats={},
            created_at=now,
            started_at=now,
        )
        session.add(run)
        await session.flush()
        session.add(
            ParserRunTask(
                run_id=run.id,
                brand_id=brand.id,
                index_type="active",
                status="done",
                attempts=1,
                hits_collected=5,
            )
        )
        old = _listing(
            100,
            run.id,
            brand.id,
            now - timedelta(days=10),
            "removed",
            "M",
            "Black",
        )
        old.last_seen_at = now - timedelta(days=2)
        current = _listing(101, run.id, brand.id, now - timedelta(days=1), "sold", "L", "Red")
        current.sold_at = now - timedelta(hours=12)
        future = _listing(102, run.id, brand.id, now, "active", "XL", "White")
        generic_a = _listing(
            103,
            run.id,
            brand.id,
            now,
            "active",
            "M",
            "Black",
            title="Rick Owens Short Sleeve T-Shirt",
            seller="seller-a",
            asset="b" * 64,
        )
        generic_b = _listing(
            104,
            run.id,
            brand.id,
            now,
            "active",
            "L",
            "White",
            title="Rick Owens Short Sleeve Tee",
            seller="seller-b",
            asset="c" * 64,
        )
        session.add_all([old, current, future, generic_a, generic_b])
        await session.commit()
        resolver = IdentityResolver(session, Settings(identity_image_requests_per_run=0))
        result = await resolver.resolve_run(run.id)
        await session.commit()
        assignments = list(
            await session.scalars(
                select(ListingModelAssignment).order_by(ListingModelAssignment.listing_id)
            )
        )
        assert len(assignments) == 5
        assert len({item.model_group_id for item in assignments[:3]}) == 1
        assert len({item.model_group_id for item in assignments[3:]}) == 2
        members_before = {
            item.listing_id: item.physical_item_id
            for item in await session.scalars(select(PhysicalItemMember))
        }
        await resolver.resolve_run(run.id)
        await session.commit()
        members_after = {
            item.listing_id: item.physical_item_id
            for item in await session.scalars(select(PhysicalItemMember))
        }
        assert members_after == members_before
        fallback_groups = [
            ModelGroup(
                stable_key=f"manual:{suffix}",
                brand_id=brand.id,
                name=suffix,
                category="footwear",
                group_type="resolved",
                created_at=now,
                updated_at=now,
            )
            for suffix in ("a", "b", "c")
        ]
        session.add_all(fallback_groups)
        await session.flush()
        for assignment, group in zip(assignments[:3], fallback_groups, strict=True):
            assignment.model_group_id = group.id
            assignment.method = "source_product_id"
        await resolver._model_candidates([old, current, future])
        match = await session.scalar(select(IdentityMatch).where(IdentityMatch.level == "physical"))
        members = list(await session.scalars(select(PhysicalItemMember)))
    assert result["linked"] == 1
    assert match is not None and match.status == "auto_confirmed"
    assert len(members) == 2
    assert future.id not in {item.listing_id for item in members}
    await engine.dispose()


def _listing(
    identifier: int,
    run_id: int,
    brand_id: int,
    created_at: datetime,
    status: str,
    size: str,
    color: str,
    *,
    title: str = "Rick Owens Geobasket",
    seller: str = "seller-hash",
    asset: str = "a" * 64,
) -> Listing:
    return Listing(
        source="grailed",
        grailed_id=identifier,
        status=status,
        url=f"https://www.grailed.com/listings/{identifier}",
        title=title,
        description=None,
        brand_name_raw="Rick Owens",
        brand_slug="rick-owens",
        brand_id=brand_id,
        category="footwear",
        subcategory=None,
        size_raw=size,
        size_normalized=size,
        condition_raw="used",
        condition="used",
        color=color,
        price=Decimal("500"),
        currency_original="USD",
        likes_count=1,
        created_at=created_at,
        updated_at=created_at,
        first_seen_at=created_at,
        last_seen_at=created_at,
        cover_photo_url="https://media-assets.grailed.com/test.jpg",
        cover_asset_key=asset,
        photo_urls=["https://media-assets.grailed.com/test.jpg"],
        photo_count=1,
        seller_identity=seller,
        seller_identity_mode="hashed",
        quality_flags=[],
        fetch_tier="T1",
        parser_run_id=run_id,
        raw_json={},
        schema_version=2,
    )
