"""T2 Algolia API executed inside one Grailed browser session."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from app.services.sources.grailed.algolia.client import AlgoliaCredentials, raise_for_status
from app.services.sources.grailed.algolia.exceptions import AlgoliaTransient, WafChallenge
from app.services.sources.grailed.algolia.models import (
    AlgoliaPage,
    AlgoliaQuery,
    AlgoliaRequest,
    FacetValue,
)
from app.services.sources.grailed.algolia.query_builder import build_params
from app.services.sources.grailed.browser.interceptor import PassiveAlgoliaInterceptor
from app.services.transport.protocols import BrowserPage, BrowserSession

_FETCH_SCRIPT = """async (p) => {
  const response = await fetch(p.url, {
    method: 'POST', headers: p.headers, body: JSON.stringify(p.body), credentials: 'omit'
  });
  return {status: response.status, text: await response.text()};
}"""


class BrowserAlgoliaClient:
    def __init__(
        self,
        browser: BrowserSession,
        credentials: AlgoliaCredentials,
        *,
        interceptor: PassiveAlgoliaInterceptor | None = None,
        grailed_url: str = "https://www.grailed.com/shop",
    ) -> None:
        self._browser = browser
        self._credentials = credentials
        self._interceptor = interceptor or PassiveAlgoliaInterceptor()
        self._grailed_url = grailed_url
        self._initialized_pages: set[int] = set()

    async def search(self, index_name: str, query: AlgoliaQuery) -> AlgoliaPage:
        payload = await self._json(
            index_name,
            f"/1/indexes/{quote(index_name, safe='')}/query",
            {"params": build_params(query)},
        )
        return AlgoliaPage.from_payload(payload)

    async def multi_query(self, requests: Sequence[AlgoliaRequest]) -> tuple[AlgoliaPage, ...]:
        pages: list[AlgoliaPage] = []
        for offset in range(0, len(requests), 8):
            batch = requests[offset : offset + 8]
            payload = await self._json(
                "*",
                "/1/indexes/*/queries",
                {
                    "requests": [
                        {"indexName": request.index_name, "params": build_params(request.query)}
                        for request in batch
                    ]
                },
            )
            results = payload.get("results", [])
            if not isinstance(results, list) or len(results) != len(batch):
                raise AlgoliaTransient("browser multi-query")
            pages.extend(
                AlgoliaPage.from_payload(result) for result in results if isinstance(result, dict)
            )
        return tuple(pages)

    async def browse(
        self, index_name: str, query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage:
        body: dict[str, Any] = {"params": build_params(query, include_cursor=True)}
        if cursor is not None:
            body["cursor"] = cursor
        payload = await self._json(
            index_name, f"/1/indexes/{quote(index_name, safe='')}/browse", body
        )
        return AlgoliaPage.from_payload(payload)

    async def search_facet_values(
        self,
        index_name: str,
        facet_name: str,
        facet_query: str,
        *,
        query: AlgoliaQuery | None = None,
    ) -> tuple[FacetValue, ...]:
        body: dict[str, Any] = {"facetQuery": facet_query}
        if query is not None:
            body["params"] = build_params(query)
        payload = await self._json(
            index_name,
            f"/1/indexes/{quote(index_name, safe='')}/facets/{quote(facet_name, safe='')}/query",
            body,
        )
        raw_hits = payload.get("facetHits", [])
        return (
            tuple(
                FacetValue(str(hit["value"]), int(hit.get("count", 0)))
                for hit in raw_hits
                if isinstance(hit, dict) and "value" in hit
            )
            if isinstance(raw_hits, list)
            else ()
        )

    async def _json(self, index_name: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        page = await self._browser.acquire_page()
        try:
            if id(page) not in self._initialized_pages:
                await page.goto(self._grailed_url, wait_until="domcontentloaded")
                self._initialized_pages.add(id(page))
            try:
                return await self._inpage(page, path, body)
            except Exception:
                return await self._interceptor.capture(
                    page,
                    navigation_url=self._grailed_url,
                    index_name=index_name,
                    request_body=body,
                )
        finally:
            await self._browser.release_page(page)
            record_request = getattr(self._browser, "record_request", None)
            if record_request is not None:
                await record_request()

    async def _inpage(self, page: BrowserPage, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "x-algolia-application-id": self._credentials.app_id,
            "x-algolia-api-key": self._credentials.api_key,
            "Content-Type": "application/json",
        }
        result = await page.evaluate(
            _FETCH_SCRIPT,
            {
                "url": f"https://{self._credentials.app_id}-dsn.algolia.net{path}",
                "headers": headers,
                "body": body,
            },
        )
        if not isinstance(result, dict):
            raise AlgoliaTransient("browser fetch")
        status = result.get("status")
        if not isinstance(status, int):
            raise AlgoliaTransient("browser fetch")
        raise_for_status(status, "browser fetch")
        try:
            payload = json.loads(str(result.get("text", "")))
        except json.JSONDecodeError as exc:
            raise WafChallenge("browser fetch", status) from exc
        if not isinstance(payload, dict):
            raise WafChallenge("browser fetch", status)
        return payload
