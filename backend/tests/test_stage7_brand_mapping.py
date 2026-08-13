"""Offline integration tests for brand facet auto-mapping."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cli import seed_mock_brands
from app.db.models import Base
from app.repositories.brands import BrandRepository
from app.services.normalization.brands import BrandMappingService, normalize_brand_name
from app.services.parser.mock.generator import ACTIVE_INDEX
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.transport.mock_http import MockHttpTransport


@dataclass(frozen=True)
class Credentials:
    app_id: str = "fixture-app"
    api_key: str = "fixture-key"
    algolia_agent: str | None = "fixture-agent"
    session_headers: tuple[tuple[str, str], ...] = ()


async def test_all_seed_brands_auto_map_with_verified_facets(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'brands.db'}"
    await seed_mock_brands(database_url)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    transport = MockHttpTransport()
    client = AlgoliaClient(transport, Credentials(), mock=True)
    async with factory() as session:
        repository = BrandRepository(session)
        summary = await BrandMappingService(
            repository, client, active_index=ACTIVE_INDEX
        ).auto_map()
        await session.commit()
        rows = await repository.list_with_counts()

    await transport.close()
    await engine.dispose()

    assert summary.processed == 21
    assert summary.verified >= 21
    assert all(any(mapping.verified for mapping in brand.source_mappings) for brand, _ in rows)
    assert normalize_brand_name("Enfants Riches Déprimés") == "enfantsrichesdeprimes"


async def test_manual_decision_and_subbrand_or_filter(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'decision.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.db.models import Brand

    async with factory() as session:
        now = datetime.now(UTC)
        brand = Brand(
            name="Rick Owens",
            slug="rick-owens",
            aliases=[],
            include_subbrands=True,
            created_at=now,
            updated_at=now,
        )
        session.add(brand)
        await session.flush()
        repository = BrandRepository(session)
        primary = await repository.upsert_candidate(
            brand_id=brand.id,
            source_name="Rick Owens",
            listings_count=100,
            score=Decimal("1"),
            verified=True,
            is_subbrand=False,
            now=now,
        )
        subbrand = await repository.upsert_candidate(
            brand_id=brand.id,
            source_name="Rick Owens DRKSHDW",
            listings_count=50,
            score=Decimal("0.90"),
            verified=False,
            is_subbrand=True,
            now=now,
        )
        await repository.decide_mapping(brand.id, subbrand.id, "confirm", now)
        await session.commit()
        loaded = await repository.get(brand.id)
        assert loaded is not None
        filters = BrandMappingService.facet_filters(loaded)
        assert primary.verified
        assert filters == (("designers.name:Rick Owens", "designers.name:Rick Owens DRKSHDW"),)
    await engine.dispose()
