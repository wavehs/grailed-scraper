"""Create durable mid-run state, then terminate without application cleanup."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import Base, Brand, BrandSourceMap
from app.repositories.runs import RunRepository
from app.services.parser.planner import ParserPlanner


async def prepare(database_url: str, marker: Path) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    async with factory() as session:
        brand = Brand(
            name="Rick Owens",
            slug="rick-owens",
            aliases=["Rick Owens"],
            include_subbrands=False,
            created_at=now,
            updated_at=now,
        )
        session.add(brand)
        await session.flush()
        session.add(
            BrandSourceMap(
                brand_id=brand.id,
                source="grailed",
                source_designer_name="Rick Owens",
                source_slug="rick-owens",
                source_designer_id=None,
                listings_count=400,
                match_score=Decimal("1"),
                match_method="manual",
                verified=True,
                is_subbrand=False,
                rejected_at=None,
                updated_at=now,
            )
        )
        await session.flush()
        plan = await ParserPlanner(session, Settings(source_mode="mock")).build(mode="full")
        repository = RunRepository(session)
        run = await repository.create(
            mode="full",
            budget=plan.budget,
            tasks=[item.persisted() for item in plan.tasks],
        )
        await repository.begin(run.id)
        tasks = await repository.tasks(run.id)
        tasks[0].status = "running"
        tasks[0].attempts = 1
        tasks[0].started_at = now
        await session.commit()
    marker.write_text("ready", encoding="ascii")
    os._exit(23)


if __name__ == "__main__":
    asyncio.run(prepare(sys.argv[1], Path(sys.argv[2])))
