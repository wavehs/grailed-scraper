"""Tier escalation, browser fetching, adaptive DOM, and robots contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from app.services.parser.fetching import TieredFetcher
from app.services.sources.grailed.algolia.exceptions import (
    AlgoliaAuthError,
    AlgoliaRateLimited,
    AlgoliaTransient,
    WafChallenge,
)
from app.services.sources.grailed.algolia.models import AlgoliaPage, AlgoliaQuery
from app.services.sources.grailed.browser.inpage_client import BrowserAlgoliaClient
from app.services.sources.grailed.discovery.models import DiscoverySeed
from app.services.sources.grailed.dom.extractor import DomExtractor
from app.services.sources.grailed.dom.robots import RobotsDenied, RobotsPolicy
from app.services.transport.protocols import BrowserPage, HttpResponse

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "grailed" / "v1"


class FakeApi:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def search(self, index_name: str, query: AlgoliaQuery) -> AlgoliaPage:
        del index_name, query
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return cast(AlgoliaPage, outcome)

    async def multi_query(self, requests: Any) -> tuple[AlgoliaPage, ...]:
        del requests
        return (await self.search("", AlgoliaQuery()),)

    async def browse(
        self, index_name: str, query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage:
        del cursor
        return await self.search(index_name, query)

    async def search_facet_values(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        del args, kwargs
        await self.search("", AlgoliaQuery())
        return ()


def empty_page() -> AlgoliaPage:
    return AlgoliaPage((), 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "errors",
    [
        [AlgoliaAuthError("search", 403)] * 3,
        [AlgoliaRateLimited("search", 429)] * 5,
        [WafChallenge("search", 200)] * 3,
    ],
)
async def test_t1_failures_escalate_without_stopping_request(errors: list[Exception]) -> None:
    refreshes = 0
    is_auth = isinstance(errors[0], AlgoliaAuthError)

    async def refresh() -> None:
        nonlocal refreshes
        refreshes += 1

    fetcher = TieredFetcher(
        {"T1": FakeApi(errors), "T2": FakeApi([empty_page()])},
        refresh_credentials=refresh,
    )
    result = await fetcher.search("index", AlgoliaQuery())
    assert result.nb_hits == 0
    assert fetcher.current_tier == "T2"
    assert fetcher.degraded_mode
    assert refreshes == (3 if is_auth else 0)


@pytest.mark.asyncio
async def test_two_t2_failures_escalate_to_t3() -> None:
    fetcher = TieredFetcher(
        {
            "T1": FakeApi([WafChallenge("x")] * 3),
            "T2": FakeApi([AlgoliaTransient("x")] * 2),
            "T3": FakeApi([empty_page()]),
        }
    )
    await fetcher.search("index", AlgoliaQuery())
    assert [transition.current for transition in fetcher.transitions] == ["T2", "T3"]


@pytest.mark.asyncio
async def test_two_canaries_return_fetching_to_t1() -> None:
    clock = 0.0
    canaries = [True, True]

    async def canary() -> bool:
        return canaries.pop(0)

    t1 = FakeApi([WafChallenge("x")] * 3 + [empty_page()])
    t2 = FakeApi([empty_page(), empty_page()])
    fetcher = TieredFetcher(
        {"T1": t1, "T2": t2}, canary=canary, canary_interval_s=5, now=lambda: clock
    )
    await fetcher.search("index", AlgoliaQuery())
    clock = 6
    await fetcher.search("index", AlgoliaQuery())
    clock = 12
    await fetcher.search("index", AlgoliaQuery())
    assert fetcher.current_tier == "T1"
    assert fetcher.transitions[-1].reason == "two successful canaries"


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed() -> None:
    fetcher = TieredFetcher({"T1": FakeApi([asyncio.CancelledError()])})
    with pytest.raises(asyncio.CancelledError):
        await fetcher.search("index", AlgoliaQuery())


class FakePage:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.gotos = 0

    async def goto(self, url: str, **kwargs: Any) -> None:
        del url, kwargs
        self.gotos += 1

    async def evaluate(self, script: str, arg: Any | None = None) -> Any:
        del script, arg
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def on(self, event: str, callback: Any) -> None:
        del event, callback

    async def wait_for_timeout(self, timeout: float) -> None:
        del timeout


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.releases = 0

    async def acquire_page(self) -> BrowserPage:
        return self.page

    async def release_page(self, page: BrowserPage) -> None:
        assert page is self.page
        self.releases += 1

    async def close(self) -> None:
        return None


class StubInterceptor:
    async def capture(self, page: Any, **kwargs: Any) -> dict[str, Any]:
        del page, kwargs
        return {"hits": [{"objectID": "2"}], "nbHits": 1}


@pytest.mark.asyncio
async def test_t2_uses_inpage_then_passive_fallback_and_releases_page() -> None:
    inpage = FakeBrowser(
        FakePage({"status": 200, "text": json.dumps({"hits": [{"objectID": "1"}], "nbHits": 1})})
    )
    client = BrowserAlgoliaClient(inpage, DiscoverySeed("APP", "key"))
    assert (await client.search("index", AlgoliaQuery())).hits[0]["objectID"] == "1"
    assert inpage.releases == 1

    fallback = FakeBrowser(FakePage(RuntimeError("evaluate failed")))
    fallback_client = BrowserAlgoliaClient(
        fallback,
        DiscoverySeed("APP", "key"),
        interceptor=StubInterceptor(),  # type: ignore[arg-type]
    )
    assert (await fallback_client.search("index", AlgoliaQuery())).hits[0]["objectID"] == "2"
    assert fallback.releases == 1


class RobotsTransport:
    def __init__(self, body: str) -> None:
        self.body = body
        self.calls = 0

    async def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        del method, kwargs
        self.calls += 1
        return HttpResponse(200, {}, self.body.encode(), url)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_robots_rules_are_cached_and_denials_are_enforced() -> None:
    transport = RobotsTransport("User-agent: *\nDisallow: /listings/private\nAllow: /")
    policy = RobotsPolicy(transport)
    await policy.require_allowed("https://www.grailed.com/shop")
    await policy.require_allowed("https://www.grailed.com/designers/example")
    assert transport.calls == 1
    with pytest.raises(RobotsDenied):
        await policy.require_allowed("https://www.grailed.com/listings/private/1")


def test_dom_extractor_prefers_embedded_json_then_adaptive_cards() -> None:
    embedded = """
    <script id="__NEXT_DATA__" type="application/json">
      {"props":{"pageProps":{"hits":[{"objectID":"9","title":"Embedded"}]}}}
    </script>
    """
    assert DomExtractor().extract(embedded)[0].payload["title"] == "Embedded"

    html = (FIXTURES / "search-page.html").read_text(encoding="utf-8")
    changed = html[html.index("<body") :].replace(
        'class="listing-card"', 'class="renamed-card"'
    )
    hits = DomExtractor().extract(changed)
    assert hits[0].object_id == "11020000"
    assert hits[0].payload["price_i"] == 23_100

    listing = (FIXTURES / "listing-page.html").read_text(encoding="utf-8")
    product = DomExtractor().extract(listing)[0]
    assert product.object_id == "11020000"
    assert product.payload["designers"][0]["name"] == "Rick Owens"
