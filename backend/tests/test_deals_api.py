"""The Deals API: listing deals, rating them, and starting a scan (v2 plan 4.1)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import Base, Brand, DealCandidate, DealScan, Listing, ModelGroup
from app.db.session import get_db
from app.main import app
from app.services.deals.scan import ScanReport
from app.services.deals.scanner import DealScanner

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


def _listing(listing_id: int, status: str = "active") -> Listing:
    return Listing(
        id=listing_id,
        grailed_id=1000 + listing_id,
        status=status,
        url=f"https://www.grailed.com/listings/{listing_id}",
        title=f"Geobasket {listing_id}",
        brand_name_raw="Rick Owens",
        brand_id=1,
        price=Decimal("300"),
        currency_original="USD",
        condition="is_gently_used",
        size_normalized="43",
        first_seen_at=NOW,
        last_seen_at=NOW,
        seller_rating_avg=Decimal("4.9"),
        seller_rating_count=20,
        fetch_tier="T1",
        raw_json={},
        schema_version=1,
    )


def _deal(deal_id: int, roi: str, verdict: str | None, hours: int) -> DealCandidate:
    return DealCandidate(
        id=deal_id,
        listing_id=deal_id,
        group_id=7,
        bucket="used",
        price=Decimal("300"),
        shipping=Decimal("15.50"),
        p25=Decimal("350"),
        p50=Decimal("500"),
        net=Decimal("90.25"),
        roi=Decimal(roi),
        liquidity_score=Decimal("70"),
        found_at=NOW - timedelta(hours=hours),
        updated_at=NOW,
        verdict=verdict,
    )


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'deals.db'}")
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
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            ModelGroup(
                id=7,
                brand_id=1,
                product_type="sneakers",
                slug="geobasket",
                name="Geobasket",
                aliases=[],
                status="confirmed",
                source="seed",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add_all([_listing(1), _listing(2), _listing(3), _listing(4, "sold")])
        await session.flush()
        session.add_all(
            [
                _deal(1, "0.30", "good", 3),
                _deal(2, "0.50", None, 2),
                _deal(3, "0.25", "bad", 1),
                _deal(4, "0.90", "good", 4),
            ]
        )
        session.add(DealScan(brand_id=1, scanned_to=NOW, hits=12, error=None))
        await session.commit()

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as http:
        yield http
    app.dependency_overrides.clear()
    if hasattr(app.state, "deal_scanner"):
        del app.state.deal_scanner
    await engine.dispose()


async def test_active_deals_come_newest_first_with_the_hit_rate(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/deals")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [3, 2, 1]
    assert body["total"] == 3
    first = body["items"][2]
    assert (first["price"], first["shipping"], first["p50"], first["net"]) == (
        30000,
        1550,
        50000,
        9025,
    )
    assert (first["brand"], first["group"], first["size"], first["verdict"]) == (
        "Rick Owens",
        "Geobasket",
        "43",
        "good",
    )
    # Ratings count every stored deal, sold ones included: 2 good of 3 rated.
    assert body["summary"] == {
        "total": 4,
        "good": 2,
        "bad": 1,
        "unrated": 1,
        "hit_rate": "0.6667",
    }
    scanner = body["scanner"]
    assert (scanner["enabled"], scanner["running"], scanner["last"]) == (False, False, None)
    assert scanner["brands"][0]["brand"] == "Rick Owens"
    assert scanner["brands"][0]["hits"] == 12


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("verdict=unrated", [2]),
        ("verdict=good&include_inactive=true", [1, 4]),
        ("sort=roi&include_inactive=true", [4, 2, 1, 3]),
        ("limit=1&offset=1", [2]),
    ],
)
async def test_deals_filter_and_sort(
    client: httpx.AsyncClient, query: str, expected: list[int]
) -> None:
    response = await client.get(f"/api/deals?{query}")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == expected


async def test_a_deal_is_rated_and_unrated(client: httpx.AsyncClient) -> None:
    rated = await client.patch("/api/deals/2", json={"verdict": "bad"})
    cleared = await client.patch("/api/deals/1", json={"verdict": None})

    assert rated.status_code == 200
    assert rated.json()["verdict"] == "bad"
    assert cleared.json()["verdict"] is None
    summary = (await client.get("/api/deals")).json()["summary"]
    assert (summary["good"], summary["bad"], summary["hit_rate"]) == (1, 2, "0.3333")


@pytest.mark.parametrize(
    ("path", "payload", "status", "code"),
    [
        ("/api/deals/99", {"verdict": "good"}, 404, "deal_not_found"),
        ("/api/deals/1", {"verdict": "great"}, 422, None),
        ("/api/deals/1", {"verdict": "good", "note": "x"}, 422, None),
    ],
)
async def test_bad_ratings_are_refused(
    client: httpx.AsyncClient, path: str, payload: dict[str, Any], status: int, code: str | None
) -> None:
    response = await client.patch(path, json=payload)

    assert response.status_code == status
    if code:
        assert response.json()["error"]["code"] == code


def _scanner(lock: asyncio.Lock, calls: list[Settings]) -> DealScanner:
    async def load(_: AsyncSession, base: Settings) -> Settings:
        return base

    async def scan(_: Any, settings: Settings, __: asyncio.Lock) -> ScanReport:
        calls.append(settings)
        return ScanReport(started_at=NOW, finished_at=NOW, brands=1, hits=0, stored=0)

    return DealScanner(
        async_sessionmaker(),
        Settings(),
        load_settings=load,
        market_lock=lock,
        grouping_lock=asyncio.Lock(),
        scan=scan,
    )


async def test_a_scan_starts_unless_the_market_is_busy(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/deals/scan")).json()["error"]["code"] == "scanner_unavailable"
    lock = asyncio.Lock()
    calls: list[Settings] = []
    app.state.deal_scanner = _scanner(lock, calls)

    async with lock:
        busy = await client.post("/api/deals/scan")
    started = await client.post("/api/deals/scan")
    await asyncio.sleep(0.05)

    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "market_busy"
    assert started.status_code == 202
    assert len(calls) == 1
    status = (await client.get("/api/deals")).json()["scanner"]
    assert status["last"]["brands"] == 1
    await app.state.deal_scanner.close()


async def test_the_deal_settings_are_editable(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        "/api/settings",
        json={"deals_enabled": True, "deals_min_margin": "0.3", "deals_scope": "watchlist"},
    )

    assert response.status_code == 200
    deals = response.json()["groups"]["deals"]
    assert deals["deals_enabled"]["value"] is True
    assert Decimal(deals["deals_min_margin"]["value"]) == Decimal("0.3")
    assert deals["deals_scope"]["origin"] == "database"
    scanner = (await client.get("/api/deals")).json()["scanner"]
    assert (scanner["enabled"], scanner["scope"]) == (True, "watchlist")


async def test_saving_a_deals_setting_wakes_the_scanner(client: httpx.AsyncClient) -> None:
    woken: list[bool] = []

    class Waker:
        def wake(self) -> None:
            woken.append(True)

    app.state.deal_scanner = Waker()

    other = await client.patch("/api/settings", json={"schedule_enabled": False})
    enabled = await client.patch("/api/settings", json={"deals_enabled": True})

    assert (other.status_code, enabled.status_code) == (200, 200)
    assert woken == [True]
