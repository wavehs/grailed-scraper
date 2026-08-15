"""Source-independent contracts for the data model and listing repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Listing, ListingPriceHistory, ParserRun
from app.domain.listings import ListingData, mark_removed_pending, resolve_removed_pending
from app.repositories.listings import ListingRepository


def make_listing(**changes: object) -> ListingData:
    """Build a complete valid normalized listing with focused test overrides."""

    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    payload: dict[str, object] = {
        "grailed_id": 1001,
        "status": "active",
        "url": "https://www.grailed.com/listings/1001",
        "title": "Archive jacket",
        "brand_name_raw": "Example Brand",
        "price": Decimal("125.00"),
        "currency_original": "usd",
        "first_seen_at": now,
        "last_seen_at": now,
        "fetch_tier": "T1",
        "parser_run_id": 1,
        "raw_json": {"id": 1001},
        "schema_version": 1,
    }
    payload.update(changes)
    return ListingData.model_validate(payload)


def test_listing_data_requires_decimal_and_valid_lifecycle_values() -> None:
    listing = make_listing()

    assert listing.price == Decimal("125.00")
    assert listing.currency_original == "USD"
    with pytest.raises(ValidationError, match="Decimal"):
        make_listing(price=125.0)
    with pytest.raises(ValidationError):
        make_listing(status="missing")
    with pytest.raises(ValidationError):
        make_listing(fetch_tier="T9")


def test_removed_pending_lifecycle_never_infers_a_sale() -> None:
    checked_at = datetime(2026, 8, 8, 12, tzinfo=UTC)

    assert mark_removed_pending("active", checked_at) == ("removed_pending", checked_at)
    assert mark_removed_pending("sold", checked_at) == ("sold", None)
    assert (
        resolve_removed_pending("removed_pending", checked_at, checked_at + timedelta(hours=47))
        == "removed_pending"
    )
    assert (
        resolve_removed_pending("removed_pending", checked_at, checked_at + timedelta(hours=48))
        == "removed"
    )


async def test_listing_upsert_preserves_first_seen_and_tracks_only_price_changes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'repository.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        session.add(
            ParserRun(
                source="grailed",
                mode="delta",
                status="running",
                dry_run=False,
                degraded_mode=False,
                tier_used="T1",
                requests_made=0,
                warnings=[],
                stats={},
                created_at=now,
            )
        )
        await session.commit()

        repository = ListingRepository(session)
        first = make_listing(
            description="The original source description",
            first_seen_at=now,
            last_seen_at=now,
        )
        initial_result = await repository.upsert_batch([first])
        await session.commit()
        original_first_seen = await session.scalar(
            select(Listing.first_seen_at).where(Listing.grailed_id == first.grailed_id)
        )

        repeated_result = await repository.upsert_batch(
            [
                make_listing(
                    first_seen_at=now + timedelta(days=1),
                    last_seen_at=now + timedelta(days=1),
                )
            ]
        )
        await session.commit()
        repeated_first_seen = await session.scalar(
            select(Listing.first_seen_at).where(Listing.grailed_id == first.grailed_id)
        )

        changed_result = await repository.upsert_batch(
            [
                make_listing(
                    price=Decimal("100.00"),
                    last_seen_at=now + timedelta(days=2),
                    first_seen_at=now + timedelta(days=2),
                )
            ]
        )
        await session.commit()
        unchanged_result = await repository.upsert_batch(
            [
                make_listing(
                    price=Decimal("100.00"),
                    last_seen_at=now + timedelta(days=3),
                    first_seen_at=now + timedelta(days=3),
                    description=None,
                )
            ]
        )
        await session.commit()

        listings = (await session.scalars(select(Listing))).all()
        history = (await session.scalars(select(ListingPriceHistory))).all()

    await engine.dispose()

    assert initial_result.inserted == 1
    assert repeated_result == repeated_result.__class__(inserted=0, updated=1, price_changes=0)
    assert changed_result.price_changes == 1
    assert unchanged_result.price_changes == 0
    assert original_first_seen == repeated_first_seen
    assert len(listings) == 1
    assert listings[0].description == "The original source description"
    assert len(history) == 1
    assert history[0].price == Decimal("100.00")
