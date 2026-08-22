"""Source-independent contracts for the phase-four UI runtime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import ApiError
from app.api.parser import (
    ClearDataRequest,
    RunRequest,
    clear_collected_data,
    clear_run_history,
    delete_run,
    start_run,
)
from app.core.config import Settings
from app.db.models import (
    Base,
    Brand,
    BrandSourceMap,
    Listing,
    SourceCredential,
    SourceSchema,
)
from app.db.session import create_database_engine
from app.repositories.runs import RunRepository
from app.services.parser.planner import ParserPlanner, _balanced_limits
from app.services.parser.runtime import ParserRuntime


class IdleRuntime:
    def active_run_ids(self) -> list[int]:
        return []


def test_progress_interval_cannot_exceed_ui_polling_contract() -> None:
    with pytest.raises(ValueError, match="at least every 2 seconds"):
        Settings(parser_progress_interval_s=3)


def test_collection_limit_uses_available_capacity() -> None:
    assert _balanced_limits([107_000, 2_900], 10_000) == [7_100, 2_900]


@pytest.mark.asyncio
async def test_run_deletion_preserves_listings_and_clear_removes_collected_data(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    engine = create_database_engine(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'cleanup.db'}")
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        brand = Brand(
            name="Cleanup Brand",
            slug="cleanup-brand",
            aliases=[],
            include_subbrands=False,
            created_at=now,
            updated_at=now,
        )
        session.add(brand)
        run = await RunRepository(session).create(
            mode="full",
            budget={},
            tasks=[{"index_type": "active", "bucket_spec": {}}],
        )
        run.status = "completed"
        listing = Listing(
            grailed_id=99,
            status="active",
            url="https://example.test/99",
            title="Keep me",
            brand_name_raw=brand.name,
            brand=brand,
            price=Decimal("100"),
            currency_original="USD",
            first_seen_at=now,
            last_seen_at=now,
            fetch_tier="T1",
            parser_run=run,
            raw_json={},
            schema_version=1,
        )
        session.add(listing)
        await session.commit()

        await delete_run(run.id, session, cast(ParserRuntime, IdleRuntime()))
        await session.refresh(listing)
        assert listing.parser_run_id is None

        history_run = await RunRepository(session).create(mode="delta", budget={}, tasks=[])
        history_run.status = "completed"
        listing.parser_run = history_run
        await session.commit()
        history_result = await clear_run_history(
            ClearDataRequest(confirm=True),
            session,
            cast(ParserRuntime, IdleRuntime()),
        )
        await session.refresh(listing)
        assert history_result.runs_deleted == 1
        assert listing.parser_run_id is None

        result = await clear_collected_data(
            ClearDataRequest(confirm=True),
            session,
            cast(ParserRuntime, IdleRuntime()),
        )
        assert result.listings_deleted == 1
        assert await session.get(Listing, listing.id) is None
        assert await session.get(Brand, brand.id) is not None
    await engine.dispose()


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
        oldest_allowed = int(
            (
                datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
                - timedelta(days=90)
            ).timestamp()
        )
        plan = await planner.build(mode="full", brand_ids=[brand.id])
        assert plan.budget["limit"] >= plan.budget["estimated_requests"] * 2
        assert plan.budget["over_limit"] is False
        for task in plan.tasks:
            assert task.query.numeric_filters[1:] == ("price_i>=400", "price_i<=5000")
            cutoff = task.query.numeric_filters[0].removeprefix("created_at_i>=")
            assert int(cutoff) >= oldest_allowed
        maximum_plan = await ParserPlanner(
            session, Settings(), collect_all=True
        ).build(mode="full", brand_ids=[brand.id])
        assert maximum_plan.budget["collect_all"] is True
        assert all(task.max_hits is None for task in maximum_plan.tasks)
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
