"""Source-independent contracts for the phase-four UI runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.parser import (
    ClearDataRequest,
    clear_collected_data,
    clear_run_history,
    delete_run,
)
from app.core.config import Settings
from app.db.models import (
    Base,
    Brand,
    BrandSourceMap,
    GroupMetric,
    Listing,
    SourceCredential,
    SourceSchema,
)
from app.db.session import create_database_engine
from app.repositories.runs import RunRepository
from app.services.grouping import GroupingService
from app.services.metrics import MetricsService
from app.services.parser.planner import ParserPlanner
from app.services.parser.runtime import ParserRuntime


class IdleRuntime:
    def active_run_ids(self) -> list[int]:
        return []


def test_progress_interval_cannot_exceed_ui_polling_contract() -> None:
    with pytest.raises(ValueError, match="at least every 2 seconds"):
        Settings(parser_progress_interval_s=3)


@pytest.mark.asyncio
async def test_run_deletion_preserves_listings_and_clear_removes_collected_data(
    tmp_path: Path,
) -> None:
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

        await GroupingService(session).regroup([brand.id])
        await MetricsService(session).recompute([brand.id])
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(GroupMetric))

        result = await clear_collected_data(
            ClearDataRequest(confirm=True),
            session,
            cast(ParserRuntime, IdleRuntime()),
        )
        assert result.listings_deleted == 1
        assert await session.get(Listing, listing.id) is None
        assert await session.get(Brand, brand.id) is not None
        assert not await session.scalar(select(func.count()).select_from(GroupMetric))
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
            await planner.build(brand_ids=[brand.id])
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
            await planner.build(brand_ids=[brand.id])
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
        oldest_sale = int(
            (
                datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
                - timedelta(days=365)
            ).timestamp()
        )
        plan = await planner.build(brand_ids=[brand.id])
        assert plan.mode == "full"
        assert plan.budget["full_brands"] == ["Rick Owens"]
        active, sold = plan.tasks
        # Active is always a complete pass; no hidden price or age filter.
        assert active.query.numeric_filters == ()
        assert active.persisted()["bucket_spec"]["sweep_missing"] is True
        assert sold.query.numeric_filters == (f"sold_at_i>={oldest_sale}",)
        banded = await ParserPlanner(
            session, Settings(collect_price_min_usd=50, collect_price_max_usd=900)
        ).build(brand_ids=[brand.id])
        assert banded.tasks[0].query.numeric_filters == ("price_i>=50", "price_i<=900")
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
    await engine.dispose()


@pytest.mark.asyncio
async def test_finished_collection_regroups_and_recomputes_metrics(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'postprocess.db'}")
    engine = create_database_engine(settings)
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
        session.add(brand)
        session.add(
            SourceCredential(
                source="grailed",
                app_id="APP",
                api_key="secret",
                active_index="active",
                sold_index="sold",
                sorted_indices=[],
                key_acl={},
                discovered_at=now,
                discovery_method="page_config",
                verification_status="valid",
            )
        )
        await session.flush()
        run = await RunRepository(session).create(
            mode="full",
            budget={},
            tasks=[{"brand_id": brand.id, "index_type": "sold", "bucket_spec": {}}],
        )
        for task in await RunRepository(session).tasks(run.id):
            task.status = "done"
        session.add(
            Listing(
                grailed_id=7,
                status="sold",
                url="https://www.grailed.com/listings/7",
                title="Rick Owens Geobasket",
                brand_name_raw=brand.name,
                brand=brand,
                category_path="footwear.hitop_sneakers",
                price=Decimal("800"),
                sold_price=Decimal("800"),
                currency_original="USD",
                created_at=now - timedelta(days=10),
                sold_at=now - timedelta(days=1),
                first_seen_at=now - timedelta(days=10),
                last_seen_at=now,
                fetch_tier="T1",
                raw_json={},
                schema_version=2,
            )
        )
        await session.commit()
        run_id = run.id

    runtime = ParserRuntime(sessions, settings)
    runtime.start(run_id)
    for _ in range(200):
        if not runtime.active_run_ids():
            break
        await asyncio.sleep(0.05)
    await runtime.close()
    async with sessions() as session:
        finished = await RunRepository(session).get(run_id)
        metric = await session.scalar(
            select(GroupMetric).where(GroupMetric.scope == "brand")
        )
        listing = await session.scalar(select(Listing).where(Listing.grailed_id == 7))
    await engine.dispose()
    assert finished is not None and finished.status == "completed"
    assert finished.stats["grouping"]["listings"] == 1
    assert finished.stats["metrics"]["rows"] >= 3
    assert listing is not None and listing.product_type == "hitop_sneakers"
    assert metric is not None and metric.sold_30d == 1
