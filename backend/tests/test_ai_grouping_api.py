"""HTTP contracts for AI grouping controls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.ai_grouping import get_ai_grouping_runtime, router
from app.api.errors import install_exception_handlers
from app.db.models import AiGroupingRun, Base
from app.db.session import get_db


class _Runtime:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def preflight(self, mode: str) -> dict[str, Any]:
        return {
            "mode": mode,
            "gemini_configured": False,
            "listing_count": 12,
            "unique_input_count": 9,
            "estimated_input_tokens": 100,
            "estimated_output_tokens": 20,
            "estimated_cost_usd": Decimal("0.000009"),
            "budget_cap_usd": Decimal("0.50"),
            "can_start": False,
            "blocked_reason": "gemini_not_configured",
            "data_fields": [
                "input_hash",
                "brand",
                "category",
                "subcategory",
                "title",
                "locked_product_type",
            ],
        }

    async def create_and_start(self, mode: str, budget: Decimal) -> int:
        now = datetime.now(UTC)
        async with self._sessions() as session:
            run = AiGroupingRun(
                mode=mode,
                status="preparing",
                base_model="gemini-2.5-flash-lite",
                review_model="gemini-2.5-flash",
                grouping_version="grouping-v1",
                prompt_version="grouping-prompt-v1",
                budget_limit_usd=budget,
                estimated_cost_usd=Decimal("0.01"),
                actual_cost_usd=Decimal(0),
                input_tokens=0,
                output_tokens=0,
                total_items=10,
                unique_requests=8,
                completed_items=0,
                ambiguous_items=0,
                failed_items=0,
                stats={},
                warnings=[],
                created_at=now,
                started_at=now,
                heartbeat_at=now,
            )
            session.add(run)
            await session.commit()
            return run.id

    async def cancel(self, _run_id: int) -> None:
        return None

    async def resume(self, _run_id: int) -> None:
        return None

    async def rollback(self, _run_id: int) -> None:
        return None


def test_ai_grouping_api_serializes_money_and_never_exposes_secret(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    import asyncio

    asyncio.run(prepare())
    application = FastAPI()
    application.include_router(router, prefix="/api")
    install_exception_handlers(application)
    runtime = _Runtime(sessions)
    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_ai_grouping_runtime] = lambda: runtime

    with TestClient(application) as client:
        preflight = client.get("/api/ai-grouping/preflight?mode=canary")
        assert preflight.status_code == 200
        assert preflight.json()["budget_cap_usd"] == "0.50"

        started = client.post(
            "/api/ai-grouping/runs",
            json={"mode": "canary", "budget_cap_usd": "0.50"},
        )
        assert started.status_code == 202
        payload = started.json()
        assert payload["budget_cap_usd"] == "0.50000000"
        assert payload["actual_cost_usd"] == "0E-8"
        assert payload["progress_percent"] == 0
        assert "api_key" not in started.text.casefold()
        assert "provider" not in started.text.casefold()

        listed = client.get("/api/ai-grouping/runs?limit=10&offset=0")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        assert client.get("/api/ai-grouping/runs/1").status_code == 200
        assert client.post("/api/ai-grouping/runs/1/cancel").status_code == 200
        assert (
            client.post(
                "/api/ai-grouping/runs/1/resume",
                json={"additional_budget_cap_usd": "0.00"},
            ).status_code
            == 202
        )
        assert client.post("/api/ai-grouping/runs/1/rollback").status_code == 200
        missing = client.get("/api/ai-grouping/runs/999")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "grouping_run_not_found"

    asyncio.run(engine.dispose())
