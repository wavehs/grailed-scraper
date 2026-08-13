"""Offline contracts for Stage 5 Grailed discovery."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.discovery import get_discovery_service
from app.core.config import Settings
from app.db.models import Base, SchemaAlert, SourceCredential, SourceSchema
from app.main import app
from app.repositories.discovery import DiscoveryRepository
from app.services.parser.mock.fake_algolia_server import FakeAlgoliaScenario
from app.services.sources.grailed.discovery.client import DiscoveryHttpError
from app.services.sources.grailed.discovery.credential_discovery import (
    capture_browser_seed,
    extract_seed_from_request,
)
from app.services.sources.grailed.discovery.js_bundle_fallback import extract_bundle_candidates
from app.services.sources.grailed.discovery.models import (
    IndexProbe,
    KeyCapabilities,
    SchemaChange,
    SchemaSample,
)
from app.services.sources.grailed.discovery.schema_sampler import compare_schemas
from app.services.sources.grailed.discovery.service import MOCK_SEED, DiscoveryService
from app.services.transport.mock_http import MockHttpTransport
from app.services.transport.protocols import BrowserPage


def test_request_extraction_reads_headers_query_body_and_facet_filters() -> None:
    body = {
        "requests": [
            {
                "indexName": "Listing_sold_production",
                "params": "facetFilters=%5B%22designers.name%3ARick+Owens%22%5D",
            },
            {"indexName": "Listing_by_date_added_production", "params": {}},
        ]
    }
    seed = extract_seed_from_request(
        "https://ABCDEFGH-dsn.algolia.net/1/indexes/*/queries?x-algolia-agent=Agent%2F1",
        headers={
            "X-Algolia-Application-Id": "ABCDEFGH",
            "X-Algolia-Api-Key": "0123456789abcdef0123456789abcdef",
            "User-Agent": "fixture-browser",
            "Accept-Language": "en-US",
            "Cookie": "session=fixture",
        },
        body=json.dumps(body),
    )

    assert seed is not None
    assert seed.app_id == "ABCDEFGH"
    assert seed.algolia_agent == "Agent/1"
    assert seed.indices == (
        "Listing_sold_production",
        "Listing_by_date_added_production",
    )
    assert seed.facet_filters == ("designers.name:Rick Owens",)
    assert dict(seed.session_headers) == {
        "user-agent": "fixture-browser",
        "accept-language": "en-US",
        "cookie": "session=fixture",
    }


def test_request_extraction_ignores_unrelated_or_incomplete_requests() -> None:
    assert extract_seed_from_request("https://example.com/api") is None
    assert extract_seed_from_request("https://APP-dsn.algolia.net/1/indexes/x/query") is None


def test_bundle_extraction_returns_candidates_without_accepting_false_shapes() -> None:
    text = """
    const config = {appId: "ABCDEFGH", searchApiKey: "0123456789abcdef0123456789abcdef"};
    const active = "Listing_production";
    const sold = "Listing_sold_production";
    const invalidKey = "not-a-search-key";
    """
    candidates = extract_bundle_candidates(text)

    assert len(candidates) == 1
    assert candidates[0].indices == ("Listing_production", "Listing_sold_production")


class _CapturedRequest:
    url = (
        "https://ABCDEFGH-dsn.algolia.net/1/indexes/*/queries"
        "?x-algolia-api-key=0123456789abcdef0123456789abcdef"
        "&x-algolia-application-id=ABCDEFGH"
    )
    headers: dict[str, str] = {}
    post_data = json.dumps(
        {"requests": [{"indexName": "Listing_production", "params": ""}]}
    )


class _CapturePage:
    def __init__(self) -> None:
        self._handler: Any = None

    def on(self, event: str, callback: Any) -> None:
        assert event == "request"
        self._handler = callback

    async def goto(self, url: str, **_: Any) -> None:
        assert "rick-owens" in url
        self._handler(_CapturedRequest())

    async def wait_for_timeout(self, timeout: float) -> None:
        assert timeout > 0
        await asyncio.sleep(0)

    async def evaluate(self, script: str, arg: Any | None = None) -> None:
        return None


class _CaptureBrowser:
    def __init__(self) -> None:
        self.page = _CapturePage()
        self.acquisitions = 0

    async def acquire_page(self) -> _CapturePage:
        self.acquisitions += 1
        return self.page

    async def release_page(self, page: BrowserPage) -> None:
        assert page is self.page

    async def close(self) -> None:
        return None


async def test_browser_capture_uses_one_page_and_returns_seed() -> None:
    browser = _CaptureBrowser()
    seed = await capture_browser_seed(browser, timeout_s=1)

    assert seed is not None
    assert seed.app_id == "ABCDEFGH"
    assert seed.indices == ("Listing_production",)
    assert browser.acquisitions == 1


@pytest.fixture
async def discovery_database(tmp_path: Any) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'discovery.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_mock_discovery_persists_probes_schema_and_uses_ttl_cache(
    discovery_database: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with discovery_database() as session:
        transport = MockHttpTransport()
        service = DiscoveryService(session, Settings(source_mode="mock"), transport)
        result = await service.refresh(force=True)
        await asyncio.sleep(0)

        async def unexpected_seed() -> Any:
            raise AssertionError("valid cache must not rediscover credentials")

        monkeypatch.setattr(service, "_discover_seed", unexpected_seed)
        cached = await service.refresh(force=False)
        await transport.close()

    assert result.status == cached.status == "ready"
    assert result.active_index == "Listing_production"
    assert result.sold_index == "Listing_sold_production"
    assert result.brand_facet == "designers.name"
    assert result.key_capabilities.can_browse is True
    assert result.pagination_limit == 1_000
    assert result.max_hits_per_page == 1_000
    assert result.schema_sample_size == 200
    assert result.schema_field_count >= 20
    assert result.expires_at == result.discovered_at + timedelta(hours=12)


async def test_parallel_refresh_is_single_flight_and_creates_one_schema(
    discovery_database: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original = DiscoveryService._discover_seed

    async def counted_seed(self: DiscoveryService) -> Any:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return await original(self)

    monkeypatch.setattr(DiscoveryService, "_discover_seed", counted_seed)
    async with discovery_database() as first_session, discovery_database() as second_session:
        first_transport = MockHttpTransport()
        second_transport = MockHttpTransport()
        try:
            first, second = await asyncio.gather(
                DiscoveryService(
                    first_session, Settings(source_mode="mock"), first_transport
                ).refresh(),
                DiscoveryService(
                    second_session, Settings(source_mode="mock"), second_transport
                ).refresh(),
            )
        finally:
            await first_transport.close()
            await second_transport.close()
    async with discovery_database() as session:
        credential_count = await session.scalar(
            select(func.count()).select_from(SourceCredential)
        )
        schema_count = await session.scalar(select(func.count()).select_from(SourceSchema))

    assert first == second
    assert calls == 1
    assert credential_count == schema_count == 1


async def test_401_or_403_marks_existing_cache_stale(
    discovery_database: async_sessionmaker[AsyncSession],
) -> None:
    async with discovery_database() as session:
        initial_transport = MockHttpTransport()
        await DiscoveryService(
            session, Settings(source_mode="mock"), initial_transport
        ).refresh()
        await initial_transport.close()
        await asyncio.sleep(0)

        failing_transport = MockHttpTransport(
            scenario=FakeAlgoliaScenario(fault="forbidden", failures=4)
        )
        service = DiscoveryService(session, Settings(source_mode="mock"), failing_transport)
        with pytest.raises(DiscoveryHttpError) as error:
            await service.refresh()
        await failing_transport.close()
        await asyncio.sleep(0)
        status = await service.status()

    assert error.value.status_code == 403
    assert status is not None and status.status == "stale"


async def test_auth_failure_triggers_exactly_one_rediscovery(
    discovery_database: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original = DiscoveryService._discover_seed

    async def counted_seed(self: DiscoveryService) -> Any:
        nonlocal calls
        calls += 1
        return await original(self)

    monkeypatch.setattr(DiscoveryService, "_discover_seed", counted_seed)
    async with discovery_database() as session:
        transport = MockHttpTransport(
            scenario=FakeAlgoliaScenario(fault="forbidden", failures=2)
        )
        try:
            result = await DiscoveryService(
                session, Settings(source_mode="mock"), transport
            ).refresh()
        finally:
            await transport.close()

    assert result.status == "ready"
    assert calls == 2


def test_schema_drift_classifies_added_removed_and_type_changes() -> None:
    previous = {
        "id": {"types": ["integer"]},
        "title": {"types": ["str"]},
        "removed": {"types": ["str"]},
    }
    current = {
        "id": {"types": ["str"]},
        "title": {"types": ["str"]},
        "added": {"types": ["boolean"]},
    }
    score, changes = compare_schemas(previous, current)

    assert score == pytest.approx(0.75)
    assert {(item.kind, item.severity) for item in changes} == {
        ("added", "info"),
        ("removed", "high"),
        ("type_changed", "high"),
    }


async def test_repeated_schema_drift_does_not_duplicate_active_alerts(
    discovery_database: async_sessionmaker[AsyncSession],
) -> None:
    change = SchemaChange(
        severity="high",
        kind="type_changed",
        path="id",
        before=("integer",),
        after=("str",),
    )
    schema = SchemaSample(
        fields={"id": {"count": 1, "frequency": 1.0, "types": ["str"], "example": "1"}},
        sample_size=1,
    )
    probe = IndexProbe("Listing_production", 1, 1_000, 1_000)
    async with discovery_database() as session:
        repository = DiscoveryRepository(session)
        for offset in (0, 1):
            await repository.persist(
                seed=MOCK_SEED,
                capabilities=KeyCapabilities(acl=("search", "browse")),
                probes=(probe,),
                active_index="Listing_production",
                sold_index=None,
                brand_facet="designers.name",
                category_facet=None,
                schema=schema,
                drift_score=1.0,
                changes=(change,),
                now=datetime.now(UTC) + timedelta(seconds=offset),
            )
        alert_count = await session.scalar(select(func.count()).select_from(SchemaAlert))
        schema_count = await session.scalar(select(func.count()).select_from(SourceSchema))

    assert alert_count == 1
    assert schema_count == 2


async def test_discovery_api_never_returns_api_key(
    discovery_database: async_sessionmaker[AsyncSession],
) -> None:
    async def override_service() -> AsyncIterator[DiscoveryService]:
        async with discovery_database() as session:
            transport = MockHttpTransport()
            try:
                yield DiscoveryService(session, Settings(source_mode="mock"), transport)
            finally:
                await transport.close()

    app.dependency_overrides[get_discovery_service] = override_service
    try:
        with TestClient(app) as client:
            refresh = client.post(
                "/api/sources/grailed/discovery/refresh", json={"force": True}
            )
            status = client.get("/api/sources/grailed/status")
    finally:
        app.dependency_overrides.clear()

    assert refresh.status_code == status.status_code == 200
    assert refresh.json()["status"] == status.json()["status"] == "ready"
    serialized = refresh.text + status.text
    assert "api_key" not in serialized.casefold()
    assert "0123456789abcdef0123456789abcdef" not in serialized
    assert refresh.json()["can_browse"] is True


async def test_expired_cache_reports_stale(
    discovery_database: async_sessionmaker[AsyncSession],
) -> None:
    async with discovery_database() as session:
        transport = MockHttpTransport()
        service = DiscoveryService(
            session, Settings(source_mode="mock", discovery_ttl_hours=1), transport
        )
        await service.refresh()
        await asyncio.sleep(0)
        credential = await session.scalar(select(SourceCredential))
        assert credential is not None
        credential.discovered_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()
        status = await service.status()
        await transport.close()
        assert status is not None and status.status == "stale"
