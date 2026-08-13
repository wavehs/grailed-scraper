"""Watermark safety and refresh lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import Base, Brand, Listing, ParserRun
from app.domain.listings import ListingData, ListingStatus
from app.repositories.lifecycle import LifecycleRepository
from app.repositories.listings import ListingRepository
from app.services.normalization.mapping import load_source_mapping
from app.services.normalization.normalizer import ListingNormalizer
from app.services.parser.incremental import IncrementalPlanner, RefreshActiveService
from app.services.parser.mock.fake_algolia_server import FakeAlgoliaScenario
from app.services.parser.mock.generator import ACTIVE_INDEX, BRANDS, SOLD_INDEX, MockCatalog
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.models import AlgoliaQuery
from app.services.transport.mock_http import MockHttpTransport


@dataclass(frozen=True)
class Credentials:
    app_id: str = "fixture-app"
    api_key: str = "fixture-key"
    algolia_agent: str | None = "fixture-agent"
    session_headers: tuple[tuple[str, str], ...] = ()


async def test_watermark_advances_only_after_complete_scope(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'watermark.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime(2026, 8, 8, tzinfo=UTC)
    async with factory() as session:
        brand = Brand(
            name="Brand", slug="brand", aliases=[], include_subbrands=False,
            created_at=now, updated_at=now,
        )
        session.add(brand)
        await session.flush()
        planner = IncrementalPlanner(LifecycleRepository(session), Settings())
        initial = await planner.plan(
            brand_id=brand.id,
            index_type="active",
            key_attr="created_at_i",
            query=AlgoliaQuery(),
        )
        assert initial.query.numeric_filters == ()
        assert not await planner.complete(
            brand_id=brand.id,
            index_type="active",
            last_key_value="10000",
            mode="full",
            coverage_complete=False,
            truncated=False,
            now=now,
        )
        assert await planner.complete(
            brand_id=brand.id,
            index_type="active",
            last_key_value="10000",
            mode="full",
            coverage_complete=True,
            truncated=False,
            now=now,
        )
        delta = await planner.plan(
            brand_id=brand.id,
            index_type="active",
            key_attr="created_at_i",
            query=AlgoliaQuery(),
        )
        assert delta.query.numeric_filters == ("created_at_i>2800",)
    await engine.dispose()


async def test_refresh_active_reappears_sells_and_confirms_removal(tmp_path) -> None:  # type: ignore[no-untyped-def]
    catalog = MockCatalog.generate(listings_per_status=2)
    active_payload = catalog.active[0]
    sold_payload = catalog.sold[0]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'refresh.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime(2026, 8, 8, tzinfo=UTC)
    transport = MockHttpTransport(catalog=catalog)
    client = AlgoliaClient(transport, Credentials(), mock=True)
    async with factory() as session:
        brand = Brand(
            name="Chrome Hearts", slug="chrome-hearts", aliases=[], include_subbrands=False,
            created_at=now, updated_at=now,
        )
        run = ParserRun(
            source="grailed", mode="delta", status="running", dry_run=False,
            degraded_mode=False, requests_made=0, warnings=[], stats={}, created_at=now,
        )
        session.add_all((brand, run))
        await session.flush()
        listing_repository = ListingRepository(session)
        await listing_repository.upsert_batch(
            [
                _listing(active_payload, brand.id, run.id, now, "active"),
                _listing(sold_payload, brand.id, run.id, now, "active"),
                _listing({"id": 999999}, brand.id, run.id, now, "active"),
            ]
        )
        await session.flush()
        service = RefreshActiveService(
            client,
            LifecycleRepository(session),
            listing_repository,
            ListingNormalizer(load_source_mapping()),
            Settings(),
            active_index=ACTIVE_INDEX,
            sold_index=SOLD_INDEX,
        )
        result = await service.run(parser_run_id=run.id, brand_id=brand.id, now=now)
        assert result.active == 1
        assert result.sold == 1
        assert result.pending == 1
        missing = await session.scalar(select(Listing).where(Listing.grailed_id == 999999))
        assert missing is not None and missing.status == "removed_pending"
        result = await service.run(
            parser_run_id=run.id,
            brand_id=brand.id,
            now=now + timedelta(hours=48),
        )
        assert result.removed == 1
        assert missing.status == "removed"
    await transport.close()
    await engine.dispose()


async def test_delta_fixture_uses_at_least_sixty_percent_fewer_requests() -> None:
    catalog = MockCatalog.generate(listings_per_status=1_000, brands=(BRANDS[0],))
    scenario = FakeAlgoliaScenario()
    transport = MockHttpTransport(catalog=catalog, scenario=scenario)
    client = AlgoliaClient(transport, Credentials(), mock=True)

    full_query = AlgoliaQuery(hits_per_page=100)
    first = await client.search(ACTIVE_INDEX, full_query)
    for page in range(1, first.nb_pages):
        await client.search(ACTIVE_INDEX, AlgoliaQuery(hits_per_page=100, page=page))
    full_requests = scenario.requests_seen

    created_values = [item["created_at_i"] for item in catalog.active]
    assert all(isinstance(value, int) for value in created_values)
    newest = max(value for value in created_values if isinstance(value, int))
    before_delta = scenario.requests_seen
    delta = await client.search(
        ACTIVE_INDEX,
        AlgoliaQuery(
            hits_per_page=100,
            numeric_filters=(f"created_at_i>{newest - 7200}",),
        ),
    )
    for page in range(1, delta.nb_pages):
        await client.search(
            ACTIVE_INDEX,
            AlgoliaQuery(
                hits_per_page=100,
                page=page,
                numeric_filters=(f"created_at_i>{newest - 7200}",),
            ),
        )
    delta_requests = scenario.requests_seen - before_delta
    await transport.close()

    assert delta_requests <= full_requests * 0.4


def _listing(
    payload: dict[str, object],
    brand_id: int,
    run_id: int,
    now: datetime,
    status: str,
) -> ListingData:
    raw_identifier = payload["id"]
    assert isinstance(raw_identifier, (int, str)) and not isinstance(raw_identifier, bool)
    identifier = int(raw_identifier)
    return ListingData(
        grailed_id=identifier,
        status=cast(ListingStatus, status),
        url=f"https://example/{identifier}",
        title=str(payload.get("title", "Missing listing")),
        brand_name_raw="Chrome Hearts",
        brand_id=brand_id,
        price=Decimal("100"),
        currency_original="USD",
        first_seen_at=now,
        last_seen_at=now,
        fetch_tier="T0",
        parser_run_id=run_id,
        raw_json=payload,
        schema_version=1,
    )
