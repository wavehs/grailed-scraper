"""HTTP contracts for brand mapping review."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.db.models import Base, Brand
from app.db.session import get_db
from app.main import app


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
