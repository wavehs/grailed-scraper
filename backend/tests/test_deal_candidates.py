"""Deals judged from the database and kept in ``deal_candidates`` (v2 plan 4.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Base,
    Brand,
    DealCandidate,
    GroupMetric,
    Listing,
    ListingModelAssignment,
    ModelGroup,
)
from app.services.deals.candidates import find_deals
from app.services.deals.rules import DealRules
from app.services.metrics.fees import load_fees
from app.services.metrics.supply import WHOLESALE_MIN_ACTIVE

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
WEEK_AGO = NOW - timedelta(days=7)
SCHEDULE = load_fees()
RULES = DealRules()


def _listing(listing_id: int, price: str, **fields: Any) -> Listing:
    values: dict[str, Any] = {
        "id": listing_id,
        "grailed_id": 1000 + listing_id,
        "status": "active",
        "url": f"https://www.grailed.com/listings/{listing_id}",
        "title": f"Sisyphus {listing_id}",
        "brand_name_raw": "Rick Owens",
        "brand_id": 1,
        "price": Decimal(price),
        "currency_original": "USD",
        "condition": "is_gently_used",
        "created_at": NOW - timedelta(days=30),
        "first_seen_at": NOW - timedelta(days=30),
        "last_seen_at": NOW - timedelta(hours=1),
        "seller_identity": f"seller-{listing_id}",
        "seller_rating_avg": Decimal("4.9"),
        "seller_rating_count": 20,
        "quality_flags": [],
        "fetch_tier": "T1",
        "raw_json": {},
        "schema_version": 1,
    }
    values.update(fields)
    return Listing(**values)


def _assign(listing_id: int) -> ListingModelAssignment:
    return ListingModelAssignment(
        listing_id=listing_id, model_group_id=7, method="rule", input_hash="x", updated_at=NOW
    )


def _metric() -> GroupMetric:
    values: dict[str, Any] = {
        "scope_key": "model:7",
        "scope": "model",
        "brand_id": 1,
        "product_type": "parka",
        "section": "outerwear",
        "group_id": 7,
        "is_line": True,
        "is_fallback": False,
        "listings": 50,
        "sold_7d": 1,
        "sold_30d": 12,
        "sold_prev_30d": 8,
        "sold_90d": 30,
        "weekly_sales": [0] * 12,
        "weekly_median_price": [None] * 12,
        "active_now": 30,
        "active_effective": 25,
        "new_listings_14d": 1,
        "sell_through_30d": Decimal("0.2"),
        "growth": Decimal("1"),
        "is_new": False,
        "colors": [],
        "sizes": [],
        "computed_at": NOW,
        "liquidity_score": Decimal("70"),
        "pricing": {"used": {"n": 30, "p25": "350.00", "p50": "500.00"}},
    }
    return GroupMetric(**values)


@pytest.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'deals.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(
            Brand(
                id=1,
                name="Rick Owens",
                slug="rick-owens",
                aliases=[],
                include_subbrands=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await db.flush()
        db.add(
            ModelGroup(
                id=7,
                brand_id=1,
                product_type="parka",
                slug="sisyphus",
                name="Sisyphus",
                aliases=[],
                status="confirmed",
                source="seed",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await db.flush()
        db.add(_metric())
        await db.commit()
        yield db
    await engine.dispose()


async def _seed(db: AsyncSession, *listings: Listing) -> None:
    db.add_all(listings)
    await db.flush()
    db.add_all(_assign(listing.id) for listing in listings)
    await db.commit()


async def _run(db: AsyncSession, **kwargs: Any) -> Any:
    options: dict[str, Any] = {"since": WEEK_AGO, "by": "seen", "now": NOW}
    options.update(kwargs)
    report = await find_deals(db, RULES, SCHEDULE, **options)
    await db.commit()
    return report


async def _stored(db: AsyncSession) -> dict[int, DealCandidate]:
    rows = (await db.scalars(select(DealCandidate))).all()
    return {row.listing_id: row for row in rows}


async def test_a_cheap_listing_is_stored_with_its_numbers(session: AsyncSession) -> None:
    await _seed(session, _listing(1, "300", ship_domestic=Decimal("15")), _listing(2, "480"))

    report = await _run(session)

    assert (report.evaluated, report.deals, report.new, report.dropped) == (2, 1, 1, 0)
    assert report.reasons == {"above_p25": 1, "thin_margin": 1}
    stored = await _stored(session)
    assert list(stored) == [1]
    deal = stored[1]
    assert (deal.group_id, deal.bucket, deal.p25, deal.p50) == (
        7,
        "used",
        Decimal("350.00"),
        Decimal("500.00"),
    )
    assert deal.shipping == Decimal("15.00")
    assert deal.roi >= RULES.min_margin
    assert deal.verdict is None


async def test_a_deal_found_again_keeps_when_it_was_found_and_its_verdict(
    session: AsyncSession,
) -> None:
    await _seed(session, _listing(1, "300"))
    await _run(session)
    await session.execute(update(DealCandidate).values(verdict="good"))
    await session.execute(update(Listing).values(price=Decimal("250")))
    await session.commit()

    report = await _run(session, now=NOW + timedelta(hours=2))

    assert (report.deals, report.new) == (1, 0)
    session.expire_all()
    deal = (await _stored(session))[1]
    assert deal.price == Decimal("250.00")
    assert deal.verdict == "good"
    assert deal.found_at.replace(tzinfo=UTC) == NOW
    assert deal.updated_at.replace(tzinfo=UTC) == NOW + timedelta(hours=2)


async def test_a_deal_that_stops_qualifying_is_dropped_unless_rated(
    session: AsyncSession,
) -> None:
    await _seed(session, _listing(1, "300"), _listing(2, "290"))
    await _run(session)
    await session.execute(
        update(DealCandidate).where(DealCandidate.listing_id == 2).values(verdict="bad")
    )
    await session.execute(update(Listing).values(price=Decimal("490")))
    await session.commit()

    report = await _run(session)

    assert (report.deals, report.dropped) == (0, 1)
    assert list(await _stored(session)) == [2]


async def test_an_unrated_deal_whose_listing_sold_is_dropped(session: AsyncSession) -> None:
    await _seed(session, _listing(1, "300"), _listing(2, "290"))
    await _run(session)
    await session.execute(
        update(DealCandidate).where(DealCandidate.listing_id == 2).values(verdict="good")
    )
    await session.execute(update(Listing).values(status="sold"))
    await session.commit()

    report = await _run(session)

    assert (report.evaluated, report.dropped) == (0, 1)
    assert list(await _stored(session)) == [2]


async def test_a_dormant_wholesaler_is_not_a_deal(session: AsyncSession) -> None:
    stock = [
        _listing(
            100 + index, "900", seller_identity="outlet", last_seen_at=NOW - timedelta(days=60)
        )
        for index in range(WHOLESALE_MIN_ACTIVE)
    ]
    await _seed(session, _listing(1, "300", seller_identity="outlet"), *stock)

    report = await _run(session)

    assert report.evaluated == 1
    assert report.deals == 0
    assert report.reasons["wholesaler"] == 1


async def test_the_window_picks_seen_or_changed_listings(session: AsyncSession) -> None:
    old = NOW - timedelta(days=30)
    await _seed(
        session,
        # Fetched by a scan an hour ago but listed long before.
        _listing(1, "300"),
        # Repriced yesterday, last fetched a month ago.
        _listing(2, "300", last_seen_at=old, price_updated_at=NOW - timedelta(days=1)),
        # Listed yesterday.
        _listing(3, "300", last_seen_at=old, created_at=NOW - timedelta(days=1)),
    )
    since = NOW - timedelta(days=2)

    seen = await _run(session, since=since, by="seen")
    changed = await _run(session, since=since, by="changed")

    assert seen.evaluated == 1
    assert changed.evaluated == 2
    assert sorted(await _stored(session)) == [1, 2, 3]


async def test_brand_scope_and_inactive_listings_are_left_out(session: AsyncSession) -> None:
    await _seed(session, _listing(1, "300"), _listing(2, "300", status="sold"))

    assert (await _run(session, brand_ids=[2])).evaluated == 0
    assert (await _run(session, brand_ids=[1])).evaluated == 1
