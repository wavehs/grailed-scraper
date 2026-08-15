"""Source-independent contracts for Algolia request construction and errors."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs

import pytest

from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.exceptions import (
    AlgoliaAuthError,
    AlgoliaBadQuery,
    AlgoliaIndexNotFound,
    AlgoliaRateLimited,
    AlgoliaTransient,
    RequestBudgetExceeded,
    WafChallenge,
)
from app.services.sources.grailed.algolia.models import AlgoliaQuery, AlgoliaRequest
from app.services.sources.grailed.algolia.query_builder import build_params
from app.services.sources.grailed.discovery.models import DiscoverySeed
from app.services.transport.protocols import HttpResponse
from app.services.transport.rate_limiter import RateLimiter


class StubTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)

    async def close(self) -> None:
        return None


def response(
    status: int,
    content: bytes,
    url: str = "https://fixture.test",
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    return HttpResponse(status, headers or {}, content, url)


def client(transport: StubTransport, **kwargs: Any) -> AlgoliaClient:
    return AlgoliaClient(
        transport,
        DiscoverySeed("APP", "very-secret-key", algolia_agent="агент / 1"),
        hosts=("https://one.test", "https://two.test"),
        requests_per_minute=60_000,
        rate_limiter=RateLimiter(60_000, 3, jitter_ratio=0),
        **kwargs,
    )


def test_query_builder_encodes_nested_quotes_and_unicode() -> None:
    encoded = build_params(
        AlgoliaQuery(
            query='Comme des Garçons "Homme"',
            facet_filters=(("designers.name:Rick Owens", "designers.name:Yohji 山本"),),
            numeric_filters=("price_i>=10000",),
        )
    )
    params = parse_qs(encoded)
    assert params["query"] == ['Comme des Garçons "Homme"']
    assert "Yohji 山本" in params["facetFilters"][0]
    assert params["analytics"] == ["false"]
    assert params["attributesToHighlight"] == ["[]"]
    assert "cursor" not in params["responseFields"][0]
    assert "cursor" in parse_qs(build_params(AlgoliaQuery(), include_cursor=True))[
        "responseFields"
    ][0]


@pytest.mark.asyncio
async def test_multi_query_chunks_requests_at_eight() -> None:
    first = b'{"results":[' + b','.join([b'{"hits":[],"nbHits":0}'] * 8) + b']}'
    second = b'{"results":[{"hits":[],"nbHits":0}]}'
    transport = StubTransport([response(200, first), response(200, second)])
    api = client(transport)

    pages = await api.multi_query(
        [AlgoliaRequest("index", AlgoliaQuery(query=str(i))) for i in range(9)]
    )

    assert len(pages) == 9
    assert [len(call["json_body"]["requests"]) for call in transport.calls] == [8, 1]


@pytest.mark.asyncio
async def test_request_budget_stops_before_the_next_network_call() -> None:
    transport = StubTransport([response(200, b'{"hits":[],"nbHits":0}')])
    api = client(transport, max_requests=1)

    await api.search("index", AlgoliaQuery(query="first"))
    with pytest.raises(RequestBudgetExceeded, match="1 requests"):
        await api.search("index", AlgoliaQuery(query="second"))

    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_transient_response_rotates_host() -> None:
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    transport = StubTransport(
        [response(503, b'{"message":"down"}'), response(200, b'{"hits":[],"nbHits":0}')]
    )
    page = await client(transport, sleep=sleep).search("index", AlgoliaQuery())
    assert page.nb_hits == 0
    assert sleeps == [1.0]
    assert [call["url"].split("/")[2] for call in transport.calls] == [
        "one.test",
        "two.test",
    ]


@pytest.mark.asyncio
async def test_auth_failure_refreshes_credentials_once_for_concurrent_requests() -> None:
    transport = StubTransport(
        [
            response(401, b'{"message":"expired"}'),
            response(403, b'{"message":"expired"}'),
            response(200, b'{"hits":[],"nbHits":0}'),
            response(200, b'{"hits":[],"nbHits":0}'),
        ]
    )
    refreshes = 0

    async def refresh_credentials() -> DiscoverySeed:
        nonlocal refreshes
        refreshes += 1
        await asyncio.sleep(0)
        return DiscoverySeed("APP", "fresh-key")

    api = client(transport, max_retries=0, refresh_credentials=refresh_credentials)
    await asyncio.gather(
        api.search("index", AlgoliaQuery(query="one")),
        api.search("index", AlgoliaQuery(query="two")),
    )

    assert refreshes == 1
    assert [call["headers"]["x-algolia-api-key"] for call in transport.calls] == [
        "very-secret-key",
        "very-secret-key",
        "fresh-key",
        "fresh-key",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error_type",
    [
        (400, AlgoliaBadQuery),
        (401, AlgoliaAuthError),
        (403, AlgoliaAuthError),
        (404, AlgoliaIndexNotFound),
        (429, AlgoliaRateLimited),
        (503, AlgoliaTransient),
    ],
)
async def test_status_codes_have_typed_secret_safe_errors(
    status: int, error_type: type[Exception]
) -> None:
    transport = StubTransport([response(status, b'{"message":"failed"}')])
    with pytest.raises(error_type) as caught:
        await client(transport, max_retries=0).search("index", AlgoliaQuery())
    assert "very-secret-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_non_json_success_is_waf_challenge() -> None:
    transport = StubTransport([response(200, b"<html>challenge</html>")])
    with pytest.raises(WafChallenge):
        await client(transport).search("index", AlgoliaQuery())


@pytest.mark.asyncio
async def test_browse_and_facet_values_use_typed_responses() -> None:
    transport = StubTransport(
        [
            response(200, b'{"hits":[{"objectID":"1"}],"nbHits":2,"cursor":"next"}'),
            response(
                200,
                '{"facetHits":[{"value":"Comme des Garçons","count":7}]}'.encode(),
            ),
        ]
    )
    api = client(transport)
    page = await api.browse("index", AlgoliaQuery(), cursor="previous")
    facets = await api.search_facet_values("index", "designers.name", "Garçons")
    assert page.cursor == "next"
    assert page.hits[0]["objectID"] == "1"
    assert facets[0].value == "Comme des Garçons"
    assert facets[0].count == 7


@pytest.mark.asyncio
async def test_rate_limit_honours_retry_after_before_retrying() -> None:
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    transport = StubTransport(
        [
            response(429, b'{"message":"slow down"}', headers={"Retry-After": "4"}),
            response(200, b'{"hits":[],"nbHits":0}'),
        ]
    )
    await client(transport, sleep=sleep).search("index", AlgoliaQuery())
    assert sleeps == [4.0]
