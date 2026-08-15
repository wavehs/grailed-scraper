"""Source-independent contracts for the phase-four UI runtime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import ApiError
from app.api.parser import RunRequest, start_run
from app.core.config import Settings
from app.db.models import (
    Base,
    Brand,
    BrandSourceMap,
    SourceCredential,
    SourceSchema,
)
from app.repositories.runs import RunRepository
from app.services.parser.planner import ParserPlanner
from app.services.parser.runtime import ParserRuntime


def test_progress_interval_cannot_exceed_ui_polling_contract() -> None:
    with pytest.raises(ValueError, match="at least every 2 seconds"):
        Settings(parser_progress_interval_s=3)


@pytest.mark.asyncio
async def test_planner_blocks_until_schema_and_mapping_exist(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'phase4.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        brand = Brand(
            name="Rick Owens",
            slug="rick-owens",
            aliases=[],
            include_subbrands=False,
            created_at=now,
            updated_at=now,
        )
        session.add_all(
            [
                brand,
                SourceCredential(
                    source="grailed",
                    app_id="APP",
                    api_key="secret",
                    active_index="active",
                    sold_index="sold",
                    sorted_indices=[],
                    key_acl={},
                    discovered_at=now,
                    discovery_method="browser",
                    verification_status="valid",
                ),
            ]
        )
        await session.flush()
        planner = ParserPlanner(session, Settings())
        with pytest.raises(RuntimeError, match="schema_required"):
            await planner.build(mode="full", brand_ids=[brand.id])
        session.add(
            SourceSchema(
                source="grailed",
                observed_fields={},
                sample_size=1,
                detected_at=now,
                drift_score=Decimal(0),
            )
        )
        await session.flush()
        with pytest.raises(RuntimeError, match="brand_mapping_required"):
            await planner.build(mode="full", brand_ids=[brand.id])
        brand.source_mappings.append(
            BrandSourceMap(
                brand_id=brand.id,
                source="grailed",
                source_designer_name="Rick Owens",
                listings_count=10,
                match_score=Decimal(1),
                match_method="manual",
                verified=True,
                is_subbrand=False,
                updated_at=now,
            )
        )
        await session.flush()
        oldest_allowed = int((datetime.now(UTC) - timedelta(days=120)).timestamp())
        plan = await planner.build(mode="full", brand_ids=[brand.id])
        for task in plan.tasks:
            assert task.query.numeric_filters[1:] == ("price_i>=400", "price_i<=5000")
            cutoff = task.query.numeric_filters[0].removeprefix("created_at_i>=")
            assert int(cutoff) >= oldest_allowed
        with pytest.raises(ApiError) as error:
            await start_run(
                RunRequest(mode="full", brand_ids=[brand.id]),
                Response(),
                session,
                Settings(live_compliance_acknowledged=True),
                cast(ParserRuntime, object()),
            )
        assert error.value.code == "dry_run_required"
        run = await RunRepository(session).create(
            mode="full",
            budget=plan.budget,
            tasks=[item.persisted() for item in plan.tasks],
        )
        persisted_task = (await RunRepository(session).tasks(run.id))[0]
        persisted_task.status = "truncated"
        run.status = "partial"
        await RunRepository(session).prepare_resume(run.id)
        assert persisted_task.status == "pending"
        assert len(plan.digest()) == 64
    await engine.dispose()
