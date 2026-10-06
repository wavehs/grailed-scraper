"""HTTP contracts for brand mapping review."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.brands import BrandServiceDependency, get_brand_service
from app.core.config import Settings, get_settings
from app.db.models import Base, Brand
from app.db.session import get_db
from app.main import app
from app.repositories.brands import BrandRepository
from app.services.normalization.brands import BrandMappingService
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.models import FacetValue
from app.services.transport.protocols import HttpTransport


def test_brand_mapping_api_happy_path_and_errors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            now = datetime.now(UTC)
            session.add(
                Brand(
                    name="Chrome Hearts",
                    slug="chrome-hearts",
                    aliases=["Chrome Hearts"],
                    include_subbrands=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    asyncio.run(prepare())
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings()
    try:
        with TestClient(app) as client:
            listed = client.get("/api/brands")
            assert listed.status_code == 200
            brand = listed.json()["data"][0]
            assert brand["status"] == "unresolved"
            assert "api_key" not in listed.text

            updated = client.patch(
                f"/api/brands/{brand['id']}",
                json={"aliases": ["CH"], "include_subbrands": True},
            )
            assert updated.status_code == 200
            assert updated.json()["aliases"] == ["CH"]

            missing = client.patch("/api/brands/999", json={"aliases": []})
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "brand_not_found"

            cors = client.options(
                "/api/brands",
                headers={
                    "Origin": "http://127.0.0.1:3000",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert cors.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


class _FakeFacets:
    async def search_facet_values(
        self, index_name: str, facet_name: str, facet_query: str, **_: object
    ) -> tuple[FacetValue, ...]:
        del index_name, facet_name
        catalog = (FacetValue("Enfants Riches Deprimes", 1226), FacetValue("Enfants Perdus", 9))
        return tuple(item for item in catalog if facet_query.casefold() in item.value.casefold())


def test_brand_can_be_added_from_designer_search_and_deleted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'brands.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def override_service(
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> BrandServiceDependency:
        # Same request-scoped session as the endpoint, like the real dependency.
        repository = BrandRepository(session)
        facets = _FakeFacets()
        return BrandServiceDependency(
            BrandMappingService(repository, facets, active_index="Listing_production"),
            repository,
            cast(HttpTransport, object()),
            cast(AlgoliaClient, facets),
            "Listing_production",
            "designers.name",
        )

    asyncio.run(prepare())
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_brand_service] = override_service
    try:
        with TestClient(app) as client:
            suggestions = client.get("/api/brands/designers", params={"q": "enfants"})
            assert suggestions.json()["data"][0] == {
                "name": "Enfants Riches Deprimes",
                "listings_count": 1226,
            }
            created = client.post(
                "/api/brands",
                json={"name": "ERD", "aliases": ["Enfants"], "designer": "Enfants Riches Deprimes"},
            )
            assert created.status_code == 201
            body = created.json()
            assert body["status"] == "verified"
            assert body["mappings"][0]["source_designer_name"] == "Enfants Riches Deprimes"
            duplicate = client.post("/api/brands", json={"name": "erd"})
            assert duplicate.json()["error"]["code"] == "brand_exists"
            unknown = client.post("/api/brands", json={"name": "Other", "designer": "Nope"})
            assert unknown.json()["error"]["code"] == "designer_not_found"
            assert client.delete(f"/api/brands/{body['id']}").status_code == 204
            assert client.get("/api/brands").json()["data"] == []
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
