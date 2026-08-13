"""Offline contracts for the deterministic T0 mock source."""

from __future__ import annotations

import time

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cli import replay_mock_smoke, seed_mock_brands
from app.core.config import Settings
from app.db.models import Brand
from app.services.parser.mock.fake_algolia_server import (
    FakeAlgoliaScenario,
    create_fake_algolia_app,
)
from app.services.parser.mock.fixtures import fixture_directory, load_catalog, load_manifest
from app.services.parser.mock.generator import ACTIVE_INDEX, BRANDS, SOLD_INDEX, MockCatalog
from app.services.transport.factory import create_http_transport
from app.services.transport.mock_http import MOCK_ALGOLIA_BASE_URL, MockHttpTransport


def test_catalog_is_deterministic_complete_and_does_not_include_seller_usernames() -> None:
    first = MockCatalog.generate()
    second = MockCatalog.generate()

    assert len(BRANDS) == 21
    assert first == second
    assert len(first.active) == len(first.sold) == 21 * 200
    identifiers = [record["id"] for record in (*first.active, *first.sold)]
    assert len(identifiers) == len(set(identifiers))
    assert all(
        record["price_i"] > 0 and "seller_username" not in record["seller"]
        for record in first.sold
    )
    assert max(record["price_i"] for record in first.sold) > 100_000


def test_versioned_fixture_assets_and_manifest_are_valid() -> None:
    manifest = load_manifest()
    fixture = fixture_directory()

    assert manifest["version"] == "v1"
    assert manifest["brands"] == 21
    assert (fixture / "sample-search.json").is_file()
    assert "listing-card" in (fixture / "search-page.html").read_text(encoding="utf-8")
    assert "listing-detail" in (fixture / "listing-page.html").read_text(encoding="utf-8")
    assert load_catalog().manifest()["sold_listings"] == 4_200


async def test_fake_algolia_supports_query_multi_browse_facets_and_key_introspection() -> None:
    app = create_fake_algolia_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fixture") as client:
        query = await client.post(
            f"/1/indexes/{SOLD_INDEX}/query",
            json={"params": "query=Rick+Owens&hitsPerPage=2&page=0"},
        )
        multi = await client.post(
            "/1/indexes/*/queries",
            json={
                "requests": [
                    {"indexName": ACTIVE_INDEX, "params": "query=Kapital&hitsPerPage=1"},
                    {"indexName": SOLD_INDEX, "params": "query=Visvim&hitsPerPage=1"},
                ]
            },
        )
        browse = await client.post(
            f"/1/indexes/{SOLD_INDEX}/browse", json={"params": "hitsPerPage=1"}
        )
        browse_next = await client.post(
            f"/1/indexes/{SOLD_INDEX}/browse",
            json={"cursor": browse.json()["cursor"], "params": "hitsPerPage=1"},
        )
        facets = await client.post(
            f"/1/indexes/{SOLD_INDEX}/facets/designers.name/query",
            json={"facetQuery": "Rick Owens"},
        )
        key = await client.get("/1/keys/fixture-key")

    response = query.json()
    assert query.status_code == 200
    assert response["nbHits"] == 200
    assert len(response["hits"]) == 2
    assert response["exhaustiveNbHits"] is True
    assert len(multi.json()["results"]) == 2
    assert browse.json()["hits"][0]["id"] != browse_next.json()["hits"][0]["id"]
    assert facets.json()["facetHits"] == [
        {"value": "Rick Owens", "highlighted": "Rick Owens", "count": 200}
    ]
    assert key.json()["acl"] == ["search", "browse"]


async def test_fake_algolia_caps_search_at_one_thousand_but_browse_continues() -> None:
    catalog = MockCatalog.generate(listings_per_status=1_100)
    app = create_fake_algolia_app(catalog)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fixture") as client:
        search = await client.post(
            f"/1/indexes/{SOLD_INDEX}/query", json={"params": "hitsPerPage=500&page=2"}
        )
        browse = await client.post(
            f"/1/indexes/{SOLD_INDEX}/browse", json={"params": "hitsPerPage=1000"}
        )

    assert search.json()["nbHits"] == 21 * 1_100
    assert search.json()["nbPages"] == 2
    assert search.json()["hits"] == []
    assert search.json()["exhaustiveNbHits"] is False
    assert len(browse.json()["hits"]) == 1_000
    assert "cursor" in browse.json()


@pytest.mark.parametrize(
    ("fault", "status_code", "content_type"),
    [
        ("forbidden", 403, "application/json"),
        ("rate_limited", 429, "application/json"),
        ("server_error", 503, "application/json"),
        ("waf", 200, "text/html"),
    ],
)
async def test_fake_algolia_fault_profiles(fault: str, status_code: int, content_type: str) -> None:
    scenario = FakeAlgoliaScenario(fault=fault, failures=1)  # type: ignore[arg-type]
    app = create_fake_algolia_app(scenario=scenario)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fixture") as client:
        first = await client.post(f"/1/indexes/{SOLD_INDEX}/query", json={"params": ""})
        second = await client.post(f"/1/indexes/{SOLD_INDEX}/query", json={"params": ""})

    assert first.status_code == status_code
    assert first.headers["content-type"].startswith(content_type)
    assert second.status_code == 200
    if fault == "rate_limited":
        assert first.headers["retry-after"] == "1"


async def test_slow_profile_delays_the_configured_first_request() -> None:
    app = create_fake_algolia_app(
        scenario=FakeAlgoliaScenario(fault="slow", failures=1, slow_delay_s=0.02)
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fixture") as client:
        started = time.monotonic()
        first = await client.get("/1/keys/fixture-key")
        elapsed = time.monotonic() - started
        second = await client.get("/1/keys/fixture-key")

    assert first.status_code == second.status_code == 200
    assert elapsed >= 0.015


async def test_mock_transport_runs_in_process_and_rejects_external_urls() -> None:
    transport = MockHttpTransport()
    try:
        response = await transport.request(
            "POST",
            f"{MOCK_ALGOLIA_BASE_URL}/1/indexes/{SOLD_INDEX}/query",
            json_body={"params": "hitsPerPage=1"},
        )
        with pytest.raises(ValueError, match="rejects external"):
            await transport.request("GET", "https://example.com/")
    finally:
        await transport.close()

    assert response.status_code == 200
    assert len(response.json()["hits"]) == 1


async def test_transport_factory_uses_network_denial_in_mock_mode() -> None:
    transport = create_http_transport(Settings(source_mode="mock"))
    assert isinstance(transport, MockHttpTransport)
    try:
        with pytest.raises(ValueError, match="rejects external"):
            await transport.request("GET", "https://algolia.net/1/keys/fixture-key")
    finally:
        await transport.close()


async def test_seed_is_idempotent_and_replay_is_offline(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}"
    assert (await seed_mock_brands(database_url)).inserted == 21
    assert (await seed_mock_brands(database_url)).inserted == 0

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(Brand)) == 21
            seeded_brand = await session.scalar(select(Brand).where(Brand.name == "Bape"))
            assert seeded_brand is not None
            seeded_brand.slug = "stale-slug"
            await session.commit()
    finally:
        await engine.dispose()

    assert (await seed_mock_brands(database_url)).updated == 1
    report = await replay_mock_smoke()
    assert report["status"] == "ok"
    assert report["requests"] == 5
