"""Completeness and pagination strategy tests against the offline fake server."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.parser.mock.fake_algolia_server import FakeAlgoliaScenario
from app.services.parser.mock.generator import (
    BRANDS,
    SOLD_INDEX,
    SOLD_SORTED_INDEX,
    MockCatalog,
)
from app.services.sources.base.models import CoverageReport
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.models import AlgoliaQuery
from app.services.sources.grailed.algolia.pagination import (
    PaginationPlanner,
    PaginationSpec,
    bisect_interval,
)
from app.services.sources.grailed.discovery.models import DiscoverySeed
from app.services.transport.mock_http import MockHttpTransport
from app.services.transport.rate_limiter import RateLimiter


def api(
    catalog: MockCatalog, scenario: FakeAlgoliaScenario
) -> tuple[AlgoliaClient, MockHttpTransport]:
    transport = MockHttpTransport(catalog=catalog, scenario=scenario)
    client = AlgoliaClient(
        transport,
        DiscoverySeed("MOCKAPP1", "fixture-key"),
        mock=True,
        requests_per_minute=60_000,
        max_retries=0,
        rate_limiter=RateLimiter(60_000, 3, jitter_ratio=0),
    )
    return client, transport


def brand_query(name: str) -> AlgoliaQuery:
    return AlgoliaQuery(facet_filters=(f"designers.name:{name}",))


@pytest.mark.asyncio
async def test_browse_collects_more_than_five_thousand_with_full_coverage() -> None:
    catalog = MockCatalog.generate(listings_per_status=5_100, brands=(BRANDS[0],))
    client, transport = api(catalog, FakeAlgoliaScenario())
    run = PaginationPlanner(client).fetch(
        PaginationSpec(
            index_name=SOLD_INDEX,
            query=brand_query(BRANDS[0].designer_name),
            can_browse=True,
            hits_per_page=1_000,
        )
    )

    batches = [batch async for batch in run]

    assert sum(len(batch.hits) for batch in batches) == 5_100
    assert run.report.coverage == Decimal("1")
    assert run.report.status == "complete"
    await transport.close()


@pytest.mark.asyncio
async def test_keyset_handles_fifteen_hundred_equal_timestamps() -> None:
    catalog = MockCatalog.generate(listings_per_status=1_500, brands=(BRANDS[1],))
    for hit in catalog.sold:
        hit["sold_at_i"] = 1_700_000_000
    client, transport = api(catalog, FakeAlgoliaScenario(acl=("search",)))
    run = PaginationPlanner(client).fetch(
        PaginationSpec(
            index_name=SOLD_INDEX,
            sorted_index=SOLD_SORTED_INDEX,
            query=brand_query(BRANDS[1].designer_name),
            strategy="keyset",
            key_attrs=("sold_at_i",),
            secondary_attrs=("id",),
        )
    )

    hits = [hit async for batch in run for hit in batch.hits]

    assert len(hits) == 1_500
    assert run.report.status == "complete"
    assert not run.report.truncated
    await transport.close()


@pytest.mark.asyncio
async def test_range_split_covers_dataset_over_search_limit() -> None:
    catalog = MockCatalog.generate(listings_per_status=1_250, brands=(BRANDS[2],))
    client, transport = api(catalog, FakeAlgoliaScenario(acl=("search",)))
    run = PaginationPlanner(client).fetch(
        PaginationSpec(
            index_name=SOLD_INDEX,
            query=brand_query(BRANDS[2].designer_name),
            strategy="range_split",
            key_attrs=("sold_at_i", "id"),
        )
    )

    hits = [hit async for batch in run for hit in batch.hits]

    assert len({hit.object_id for hit in hits}) == 1_250
    assert run.report.coverage == Decimal("1")
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["browse", "range_split"])
async def test_checkpoint_resume_continues_without_duplicate_ids(strategy: str) -> None:
    catalog = MockCatalog.generate(listings_per_status=1_250, brands=(BRANDS[3],))
    client, transport = api(catalog, FakeAlgoliaScenario())
    spec = PaginationSpec(
        index_name=SOLD_INDEX,
        query=brand_query(BRANDS[3].designer_name),
        strategy=strategy,  # type: ignore[arg-type]
        can_browse=strategy == "browse",
        key_attrs=("sold_at_i", "id"),
        hits_per_page=200,
    )
    first_run = PaginationPlanner(client).fetch(spec)
    iterator = first_run.__aiter__()
    first = await anext(iterator)
    assert first.cursor is not None

    resumed = PaginationPlanner(client).fetch(
        PaginationSpec(
            index_name=spec.index_name,
            query=spec.query,
            strategy=spec.strategy,
            can_browse=spec.can_browse,
            key_attrs=spec.key_attrs,
            hits_per_page=spec.hits_per_page,
            resume_cursor=first.cursor,
        )
    )
    remainder = [hit async for batch in resumed for hit in batch.hits]
    identifiers = {hit.object_id for hit in first.hits} | {
        hit.object_id for hit in remainder
    }
    assert len(identifiers) == 1_250
    await transport.close()


@pytest.mark.parametrize(
    "expected,collected,truncated,status",
    [
        (100, 98, False, "complete"),
        (100, 97, False, "partial"),
        (100, 70, True, "partial"),
        (100, 69, False, "poor"),
        (0, 0, False, "skipped"),
    ],
)
def test_coverage_thresholds(
    expected: int, collected: int, truncated: bool, status: str
) -> None:
    report = CoverageReport.calculate(
        expected_hits=expected, collected_hits=collected, truncated=truncated
    )
    assert report.status == status


@given(
    lo=st.integers(min_value=0, max_value=10**9),
    width=st.integers(min_value=2, max_value=10**9),
)
def test_numeric_range_bisection_has_no_gaps_or_overlap(lo: int, width: int) -> None:
    hi = lo + width
    lower, upper = bisect_interval(lo, hi)
    assert lower[0] == lo
    assert lower[1] == upper[0]
    assert upper[1] == hi
