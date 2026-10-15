"""Coverage stays honest when Algolia's nbHits is only an estimate."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, ParserRun, ParserRunTask
from app.db.session import get_db
from app.main import app
from app.services.sources.base.models import CoverageReport
from app.services.sources.grailed.algolia.models import AlgoliaPage, AlgoliaQuery, AlgoliaRequest
from app.services.sources.grailed.algolia.pagination import PaginationPlanner, PaginationSpec

_FILTER = re.compile(r"^(\w+)(>=|<)(\d+)$")


class _RangeIndex:
    """An index that filters on ``id`` ranges, like Algolia's numeric filters."""

    def __init__(
        self,
        ids: Sequence[int],
        *,
        top_nb_hits: int,
        exhaustive: bool,
        vanished: frozenset[int] = frozenset(),
    ) -> None:
        self._ids = sorted(ids)
        self._top_nb_hits = top_nb_hits
        self._exhaustive = exhaustive
        # Counted by probes but gone by the time the page is read (sold, deleted).
        self._vanished = vanished

    def _matching(self, query: AlgoliaQuery) -> list[int]:
        ids = self._ids
        for item in query.numeric_filters:
            match = _FILTER.match(item)
            assert match is not None, item
            _, operator, raw = match.groups()
            bound = int(raw)
            ids = [i for i in ids if (i >= bound if operator == ">=" else i < bound)]
        return ids

    async def search(self, _index_name: str, query: AlgoliaQuery) -> AlgoliaPage:
        if not query.numeric_filters and query.hits_per_page == 0:
            return AlgoliaPage((), nb_hits=self._top_nb_hits, exhaustive_nb_hits=self._exhaustive)
        ids = self._matching(query)
        if query.hits_per_page == 0:
            return AlgoliaPage((), nb_hits=len(ids))
        visible = [i for i in ids if i not in self._vanished]
        start = query.page * query.hits_per_page
        page = visible[start : start + query.hits_per_page]
        return AlgoliaPage(
            tuple({"objectID": str(i), "id": i} for i in page), nb_hits=len(ids)
        )

    async def multi_query(self, requests: list[AlgoliaRequest]) -> tuple[AlgoliaPage, ...]:
        return tuple([await self.search(item.index_name, item.query) for item in requests])

    async def browse(
        self, _index_name: str, _query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage:
        raise AssertionError("range split never browses")


def _spec() -> PaginationSpec:
    return PaginationSpec(
        index_name="live",
        query=AlgoliaQuery(),
        strategy="range_split",
        secondary_attrs=("id",),
        key_attrs=(),
        pagination_limit=1_000,
        hits_per_page=200,
    )


async def _collect(index: _RangeIndex) -> CoverageReport:
    run = PaginationPlanner(index).fetch(_spec())
    async for _ in run:
        pass
    return run.report


IDS = range(1, 3_001)


@pytest.mark.asyncio
async def test_an_inexact_nbhits_is_replaced_by_the_sum_of_range_probes() -> None:
    report = await _collect(_RangeIndex(IDS, top_nb_hits=5_000, exhaustive=False))

    assert report.expected_hits == 3_000
    assert report.collected_hits == 3_000
    assert report.expected_source == "estimated"
    assert report.coverage == Decimal(1)


@pytest.mark.asyncio
async def test_coverage_below_the_estimate_is_reported_not_reconciled_away() -> None:
    vanished = frozenset(range(100, 200))
    index = _RangeIndex(IDS, top_nb_hits=5_000, exhaustive=False, vanished=vanished)

    report = await _collect(index)

    # The old reconciliation set expected := collected and always showed 100%.
    assert report.expected_hits == 3_000
    assert report.collected_hits == 2_900
    assert report.coverage == Decimal(2_900) / Decimal(3_000)
    assert report.status == "partial"
    assert any("estimated" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_an_exact_nbhits_stays_the_expected_count() -> None:
    report = await _collect(_RangeIndex(IDS, top_nb_hits=3_000, exhaustive=True))

    assert report.expected_hits == 3_000
    assert report.expected_source == "exact"
    assert report.warnings == ()


def test_the_coverage_report_defaults_to_an_exact_expected_count() -> None:
    report = CoverageReport.calculate(expected_hits=10, collected_hits=9)

    assert report.expected_source == "exact"
    estimated = CoverageReport.calculate(
        expected_hits=10, collected_hits=12, expected_source="estimated"
    )
    assert estimated.coverage == Decimal(1) and estimated.expected_source == "estimated"


async def _estimated_run(path: Path) -> tuple[async_sessionmaker[AsyncSession], int]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 15, tzinfo=UTC)
    async with factory() as session:
        run = ParserRun(
            source="grailed",
            mode="full",
            status="completed",
            dry_run=False,
            degraded_mode=False,
            requests_made=3,
            warnings=[],
            stats={"fetching": {"tasks": 1, "estimated": 1}},
            coverage_avg=Decimal("0.96667"),
            created_at=now,
        )
        session.add(run)
        await session.flush()
        session.add(
            ParserRunTask(
                run_id=run.id,
                index_type="sold",
                status="done",
                attempts=1,
                hits_collected=2_900,
                expected_hits=3_000,
                expected_source="estimated",
                coverage=Decimal("0.96667"),
            )
        )
        await session.commit()
        return factory, run.id


def test_the_api_marks_estimated_coverage(tmp_path: Path) -> None:
    factory, run_id = asyncio.run(_estimated_run(tmp_path / "api.db"))

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        detail = client.get(f"/api/parser/runs/{run_id}").json()
        progress = client.get(f"/api/parser/runs/{run_id}/progress").json()
        runs = client.get("/api/parser/runs").json()
    finally:
        app.dependency_overrides.clear()

    assert detail["run"]["coverage_is_estimated"] is True
    assert detail["tasks"][0]["expected_source"] == "estimated"
    assert progress["coverage_is_estimated"] is True
    assert progress["tasks"][0]["expected_source"] == "estimated"
    assert runs["data"][0]["coverage_is_estimated"] is True
