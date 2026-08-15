"""Degraded Algolia-compatible façade over Tier-three HTML pages."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from urllib.parse import quote_plus

from app.services.sources.grailed.algolia.models import AlgoliaPage, AlgoliaQuery, AlgoliaRequest
from app.services.sources.grailed.dom.extractor import DomExtractor
from app.services.sources.grailed.dom.robots import RobotsPolicy
from app.services.transport.protocols import BrowserSession


class DomAlgoliaClient:
    def __init__(
        self,
        browser: BrowserSession,
        robots: RobotsPolicy,
        *,
        extractor: DomExtractor | None = None,
        search_url: str = "https://www.grailed.com/shop",
    ) -> None:
        self._browser = browser
        self._robots = robots
        self._extractor = extractor or DomExtractor()
        self._search_url = search_url

    async def search(self, index_name: str, query: AlgoliaQuery) -> AlgoliaPage:
        del index_name
        url = f"{self._search_url}?query={quote_plus(query.query)}"
        hits = await self._fetch(url)
        start = query.page * query.hits_per_page
        visible = hits[start : start + query.hits_per_page] if query.hits_per_page else ()
        return AlgoliaPage(
            hits=visible,
            nb_hits=len(hits),
            page=query.page,
            nb_pages=1 if hits else 0,
            hits_per_page=query.hits_per_page,
        )

    async def multi_query(self, requests: Sequence[AlgoliaRequest]) -> tuple[AlgoliaPage, ...]:
        return tuple([await self.search(request.index_name, request.query) for request in requests])

    async def browse(
        self, index_name: str, query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage:
        if cursor is not None:
            return AlgoliaPage((), 0)
        return await self.search(index_name, replace(query, page=0))

    async def _fetch(self, url: str) -> tuple[dict[str, object], ...]:
        await self._robots.require_allowed(url)
        page = await self._browser.acquire_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            html = await page.evaluate("() => document.documentElement.outerHTML")
            if not isinstance(html, str):
                return ()
            return tuple(hit.payload for hit in self._extractor.extract(html, url=url))
        finally:
            await self._browser.release_page(page)
