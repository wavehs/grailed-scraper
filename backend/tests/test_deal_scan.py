"""The deal scanner: what it asks Grailed, what it stores, and when it runs (v2 plan 4.1)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import (
    Base,
    Brand,
    BrandSourceMap,
    DealScan,
    GroupMetric,
    Listing,
    ModelGroup,
    SourceCredential,
    WatchlistItem,
)
from app.services.deals import scan as scan_module
from app.services.deals.scan import (
    LOOKBACK,
    OVERLAP,
    ScanReport,
    brands_in_scope,
    run_scan,
)
from app.services.deals.scanner import DealScanner
from app.services.sources.grailed.algolia.models import AlgoliaPage, AlgoliaQuery

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
CREDENTIAL = SourceCredential(
    source="grailed",
    app_id="APP",
    api_key="key",
    active_index="Listing_production",
    brand_facet="designers.name",
    pagination_limit=1000,
    max_hits_per_page=1000,
)
HIT: dict[str, Any] = {
    "objectID": "501",
    "title": "Archive bomber",
    "designers": [{"name": "Rick Owens", "slug": "rick-owens"}],
    "price": 450,
    "user": {"seller_score": {"rating_average": 4.9, "rating_count": 20}},
}


def _brand(brand_id: int, name: str) -> Brand:
    return Brand(
        id=brand_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        aliases=[],
        include_subbrands=False,
        created_at=NOW,
        updated_at=NOW,
    )


def _metric(brand_id: int, group_id: int, score: str) -> GroupMetric:
    values: dict[str, Any] = {
        "scope_key": f"model:{group_id}",
        "scope": "model",
        "brand_id": brand_id,
        "product_type": "parka",
        "section": "outerwear",
        "group_id": group_id,
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
        "liquidity_score": Decimal(score),
    }
    return GroupMetric(**values)


def _group(group_id: int, brand_id: int) -> ModelGroup:
    return ModelGroup(
        id=group_id,
        brand_id=brand_id,
        product_type="parka",
        slug=f"model-{group_id}",
        name=f"Model {group_id}",
        aliases=[],
        status="confirmed",
        source="seed",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scan.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([_brand(1, "Rick Owens"), _brand(2, "Balenciaga"), _brand(3, "Prada")])
        await session.flush()
        session.add(
            BrandSourceMap(
                brand_id=1,
                source_designer_name="Rick Owens",
                match_score=Decimal(1),
                match_method="exact",
                verified=True,
                updated_at=NOW,
            )
        )
        session.add_all([_group(7, 1), _group(8, 2), _group(9, 3)])
        await session.flush()
        # Rick Owens and Balenciaga hold a liquid model; Balenciaga has no verified mapping.
        session.add_all([_metric(1, 7, "70"), _metric(2, 8, "60"), _metric(3, 9, "10")])
        session.add(WatchlistItem(group_id=9, added_at=NOW))
        await session.commit()
    yield factory
    await engine.dispose()


class FakeSearch:
    def __init__(self, pages: list[list[dict[str, Any]]], fail: bool = False) -> None:
        self.pages = pages
        self.fail = fail
        self.calls: list[tuple[str, AlgoliaQuery]] = []

    async def __call__(self, index: str, query: AlgoliaQuery) -> AlgoliaPage:
        self.calls.append((index, query))
        if self.fail:
            raise RuntimeError("throttled")
        hits = self.pages[query.page] if query.page < len(self.pages) else []
        return AlgoliaPage(hits=tuple(hits), nb_hits=99, page=query.page, nb_pages=len(self.pages))


class TimedSearch:
    """Hits stamped with ``created_at_i``; answers the scanner's range filter like Algolia."""

    def __init__(self, stamps: list[int]) -> None:
        self.hits = [
            {**HIT, "objectID": str(700 + index), "created_at_i": stamp}
            for index, stamp in enumerate(stamps)
        ]
        self.calls: list[AlgoliaQuery] = []

    async def __call__(self, _: str, query: AlgoliaQuery) -> AlgoliaPage:
        self.calls.append(query)
        assert query.filters is not None
        low, high = (
            int(part) for part in query.filters.split(" OR ")[0].split(":")[1].split(" TO ")
        )
        found = [hit for hit in self.hits if low <= hit["created_at_i"] <= high]
        size = query.hits_per_page
        page = found[query.page * size : (query.page + 1) * size]
        pages = -(-len(found) // size)
        return AlgoliaPage(hits=tuple(page), nb_hits=len(found), page=query.page, nb_pages=pages)


def _span(since: datetime) -> str:
    low, high = int(since.timestamp()) + 1, int(NOW.timestamp())
    return f"created_at_i:{low} TO {high} OR price_updated_at_i:{low} TO {high}"


async def _scan(
    sessions: async_sessionmaker[AsyncSession],
    search: Any,
    credential: SourceCredential = CREDENTIAL,
    **overrides: Any,
) -> ScanReport:
    return await run_scan(
        sessions,
        Settings(**overrides),
        search,
        credential,
        grouping_lock=asyncio.Lock(),
        now=NOW,
    )


def _small_pages() -> SourceCredential:
    return SourceCredential(
        source="grailed",
        app_id="APP",
        api_key="key",
        active_index="Listing_production",
        brand_facet="designers.name",
        pagination_limit=2,
        max_hits_per_page=2,
    )


async def test_the_scope_is_liquid_brands_or_the_watched_ones(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        liquid = await brands_in_scope(session, Settings())
        watched = await brands_in_scope(session, Settings(deals_scope="watchlist"))

    assert liquid == [1, 2]
    assert watched == [3]


async def test_a_scan_asks_for_what_changed_since_a_day_ago(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    search = FakeSearch([[HIT]])

    report = await _scan(sessions, search)

    # Balenciaga has no verified designer mapping, so only Rick Owens is asked for.
    assert [(index, query.page) for index, query in search.calls] == [("Listing_production", 0)]
    query = search.calls[0][1]
    assert query.filters == _span(NOW - LOOKBACK)
    assert query.facet_filters == (("designers.name:Rick Owens",),)
    assert (report.brands, report.hits, report.stored, report.failed) == (1, 1, 1, {})
    assert report.deals is not None
    async with sessions() as session:
        listing = await session.scalar(select(Listing))
        scan = await session.get(DealScan, 1)
    assert listing is not None
    assert (listing.grailed_id, listing.status, listing.parser_run_id) == (501, "active", None)
    assert scan is not None
    assert scan.scanned_to.replace(tzinfo=UTC) == NOW
    assert (scan.hits, scan.error) == (1, None)


async def test_the_next_scan_starts_a_little_before_the_last_one(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        session.add(DealScan(brand_id=1, scanned_to=NOW - timedelta(minutes=20), hits=3))
        await session.commit()
    search = FakeSearch([[]])

    report = await _scan(sessions, search)

    assert search.calls[0][1].filters == _span(NOW - timedelta(minutes=20) - OVERLAP)
    assert (report.hits, report.deals) == (0, None)


async def test_a_window_over_the_readable_cap_is_halved_until_it_fits(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    start = int((NOW - LOOKBACK).timestamp())
    search = TimedSearch([start + hours * 3600 for hours in (1, 2, 9, 13, 20)])

    report = await _scan(sessions, search, _small_pages())

    assert (report.hits, report.stored, report.truncated) == (5, 5, ())
    assert len(search.calls) > 1
    async with sessions() as session:
        scan = await session.get(DealScan, 1)
    assert scan is not None
    assert scan.scanned_to.replace(tzinfo=UTC) == NOW


async def test_hits_too_close_to_split_are_reported_as_truncated(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    moment = int((NOW - timedelta(hours=3)).timestamp())
    search = TimedSearch([moment, moment, moment])

    report = await _scan(sessions, search, _small_pages())

    assert report.truncated == ("Rick Owens",)
    assert report.stored == 2


async def test_a_failed_brand_keeps_where_the_last_scan_stopped(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    before = NOW - timedelta(hours=2)
    async with sessions() as session:
        session.add(DealScan(brand_id=1, scanned_to=before, hits=3))
        await session.commit()

    report = await _scan(sessions, FakeSearch([], fail=True))

    assert report.failed == {"Rick Owens": "RuntimeError: throttled"}
    async with sessions() as session:
        scan = await session.get(DealScan, 1)
    assert scan is not None
    assert scan.scanned_to.replace(tzinfo=UTC) == before
    assert scan.error == "RuntimeError: throttled"


def _report() -> ScanReport:
    return ScanReport(started_at=NOW, finished_at=NOW, brands=1, hits=2, stored=2)


class FakeScan:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def __call__(self, *_: Any) -> ScanReport:
        self.calls += 1
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return _report()


def _scanner(
    sessions: async_sessionmaker[AsyncSession],
    scan: FakeScan,
    lock: asyncio.Lock | None = None,
    **overrides: Any,
) -> DealScanner:
    async def load(_: AsyncSession, base: Settings) -> Settings:
        return base

    return DealScanner(
        sessions,
        Settings(**overrides),
        load_settings=load,
        market_lock=lock or asyncio.Lock(),
        grouping_lock=asyncio.Lock(),
        scan=scan,
        startup_delay_s=0,
    )


async def test_a_disabled_scanner_scans_only_when_asked(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    scan = FakeScan()
    scanner = _scanner(sessions, scan)

    assert await scanner.tick() is None
    assert await scanner.tick(force=True) == _report()
    assert scan.calls == 1
    assert scanner.status()["last"]["stored"] == 2


async def test_a_running_collection_skips_the_cycle(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    lock = asyncio.Lock()
    scan = FakeScan()
    scanner = _scanner(sessions, scan, lock, deals_enabled=True)

    async with lock:
        assert scanner.trigger() is False
        assert await scanner.tick() is None

    assert scan.calls == 0
    assert scanner.status()["skipped_at"] is not None


async def test_a_failed_scan_is_kept_as_the_last_error(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    scanner = _scanner(sessions, FakeScan(RuntimeError("discovery_required")), deals_enabled=True)

    assert await scanner.tick() is None

    assert scanner.status()["last_error"] == "RuntimeError: discovery_required"
    assert scanner.running is False


async def test_a_triggered_scan_runs_in_the_background(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    scan = FakeScan()
    scanner = _scanner(sessions, scan)

    assert scanner.trigger() is True
    assert scanner.trigger() is False
    await asyncio.sleep(0.05)
    await scanner.close()

    assert scan.calls == 1


async def test_an_enabled_scanner_scans_on_its_own(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    scan = FakeScan()
    scanner = _scanner(sessions, scan, deals_enabled=True)

    scanner.start()
    await asyncio.sleep(0.05)
    await scanner.close()

    assert scan.calls == 1


async def test_a_failed_judgement_keeps_the_cursor_so_the_window_is_read_again(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = NOW - timedelta(hours=2)
    async with sessions() as session:
        session.add(DealScan(brand_id=1, scanned_to=before, hits=3))
        await session.commit()

    async def locked(*_: Any, **__: Any) -> Any:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(scan_module, "find_deals", locked)

    report = await _scan(sessions, FakeSearch([[HIT]]))

    assert report.failed == {"Rick Owens": "judge: RuntimeError: database is locked"}
    assert (report.stored, report.deals) == (1, None)
    async with sessions() as session:
        scan = await session.get(DealScan, 1)
    assert scan is not None
    assert scan.scanned_to.replace(tzinfo=UTC) == before
    assert scan.error == "judge: RuntimeError: database is locked"


async def test_only_brands_with_new_listings_are_regrouped(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    regrouped: list[list[int]] = []

    async def regroup(_: AsyncSession, brand_ids: list[int]) -> None:
        regrouped.append(list(brand_ids))

    monkeypatch.setattr(scan_module, "regroup_brands", regroup)

    await _scan(sessions, FakeSearch([[HIT]]))
    report = await _scan(sessions, FakeSearch([[{**HIT, "price": 400}]]))

    assert regrouped == [[1]]
    assert report.deals is not None


async def test_a_streak_of_failed_brands_ends_the_pass(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with sessions() as session:
        session.add(
            BrandSourceMap(
                brand_id=2,
                source_designer_name="Balenciaga",
                match_score=Decimal(1),
                match_method="exact",
                verified=True,
                updated_at=NOW,
            )
        )
        await session.commit()
    monkeypatch.setattr(scan_module, "FAILURE_STREAK", 1)
    search = FakeSearch([], fail=True)

    report = await _scan(sessions, search)

    assert len(search.calls) == 1
    assert report.aborted is True
    assert list(report.failed) == ["Rick Owens"]


async def test_a_triggered_scan_shows_as_running_at_once(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    scanner = _scanner(sessions, FakeScan())

    assert scanner.trigger() is True

    assert scanner.status()["running"] is True
    await asyncio.sleep(0.05)
    assert scanner.status()["running"] is False
    await scanner.close()


async def test_a_manual_scan_that_cannot_read_settings_keeps_the_error(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async def broken(_: AsyncSession, __: Settings) -> Settings:
        raise RuntimeError("settings unreadable")

    scanner = DealScanner(
        sessions,
        Settings(),
        load_settings=broken,
        market_lock=asyncio.Lock(),
        grouping_lock=asyncio.Lock(),
        scan=FakeScan(),
        startup_delay_s=0,
    )

    assert scanner.trigger() is True
    await asyncio.sleep(0.05)

    assert scanner.status()["last_error"] == "RuntimeError: settings unreadable"
    assert scanner.running is False
    await scanner.close()


async def test_waking_the_scanner_skips_its_wait(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    scan = FakeScan()

    async def load(_: AsyncSession, base: Settings) -> Settings:
        return base

    scanner = DealScanner(
        sessions,
        Settings(deals_enabled=True),
        load_settings=load,
        market_lock=asyncio.Lock(),
        grouping_lock=asyncio.Lock(),
        scan=scan,
        startup_delay_s=3600,
    )

    scanner.start()
    await asyncio.sleep(0.01)
    scanner.wake()
    await asyncio.sleep(0.05)
    await scanner.close()

    assert scan.calls == 1
