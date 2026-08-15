"""Extract public Algolia search credentials from captured browser traffic."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from app.services.sources.grailed.discovery.models import DiscoverySeed
from app.services.transport.protocols import BrowserSession

SEARCH_URL = "https://www.grailed.com/designers/rick-owens?query=hoodie"


def extract_seed_from_request(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: str | bytes | Mapping[str, Any] | None = None,
) -> DiscoverySeed | None:
    """Parse one Algolia request without exposing its secret in diagnostics."""

    parsed = urlparse(url)
    if "algolia" not in parsed.netloc.casefold() and "/1/indexes/" not in parsed.path:
        return None
    normalized_headers = {key.casefold(): value for key, value in (headers or {}).items()}
    query = parse_qs(parsed.query)
    app_id = normalized_headers.get("x-algolia-application-id") or _first(
        query, "x-algolia-application-id"
    )
    api_key = normalized_headers.get("x-algolia-api-key") or _first(query, "x-algolia-api-key")
    agent = normalized_headers.get("x-algolia-agent") or _first(query, "x-algolia-agent")
    if not app_id:
        app_id = (
            parsed.netloc.split("-", maxsplit=1)[0] if "-dsn.algolia" in parsed.netloc else None
        )
    if not app_id or not api_key:
        return None
    indices, filters = _request_metadata(body)
    session_headers = tuple(
        (name, value)
        for name in ("user-agent", "accept-language", "cookie")
        if (value := normalized_headers.get(name))
    )
    return DiscoverySeed(
        app_id=app_id,
        api_key=api_key,
        algolia_agent=unquote(agent) if agent else None,
        indices=tuple(dict.fromkeys(indices)),
        facet_filters=tuple(dict.fromkeys(filters)),
        session_headers=session_headers,
    )


async def capture_browser_seed(
    browser: BrowserSession, *, timeout_s: float
) -> DiscoverySeed | None:
    """Navigate once and capture the first complete Algolia request."""

    captured: list[DiscoverySeed] = []
    pending: set[asyncio.Task[None]] = set()

    async def inspect_request(request: Any) -> None:
        url = await _value(request, "url", "")
        headers = await _value(request, "headers", {})
        body = await _value(request, "post_data", None)
        if isinstance(url, str) and isinstance(headers, Mapping):
            seed = extract_seed_from_request(url, headers=headers, body=body)
            if seed is not None:
                captured.append(seed)

    def handler(request: Any) -> None:
        task = asyncio.create_task(inspect_request(request))
        pending.add(task)
        task.add_done_callback(pending.discard)

    page = await browser.acquire_page()
    try:
        page.on("request", handler)
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
        await page.wait_for_timeout(min(int(timeout_s * 1000), 4_000))
        if pending:
            await asyncio.gather(*pending)
    finally:
        await browser.release_page(page)
    return _merge_seeds(captured)


async def _value(owner: Any, name: str, default: Any) -> Any:
    value = getattr(owner, name, default)
    if callable(value):
        value = value()
    return await value if inspect.isawaitable(value) else value


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _request_metadata(
    body: str | bytes | Mapping[str, Any] | None,
) -> tuple[list[str], list[str]]:
    if body is None:
        return [], []
    try:
        payload: Any = body if isinstance(body, Mapping) else json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return [], []
    requests = payload.get("requests", []) if isinstance(payload, Mapping) else []
    indices: list[str] = []
    filters: list[str] = []
    for item in requests if isinstance(requests, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = item.get("indexName")
        if isinstance(name, str):
            indices.append(name)
        params = item.get("params", {})
        if isinstance(params, str):
            params = {key: values[-1] for key, values in parse_qs(params).items()}
        if isinstance(params, Mapping):
            raw_filters = params.get("facetFilters", [])
            if isinstance(raw_filters, str):
                try:
                    raw_filters = json.loads(raw_filters)
                except json.JSONDecodeError:
                    raw_filters = []
            filters.extend(_flatten_strings(raw_filters))
    return indices, filters


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [part for item in value for part in _flatten_strings(item)]
    return []


def _merge_seeds(seeds: list[DiscoverySeed]) -> DiscoverySeed | None:
    if not seeds:
        return None
    first = seeds[0]
    compatible = [
        item for item in seeds if item.app_id == first.app_id and item.api_key == first.api_key
    ]
    return DiscoverySeed(
        app_id=first.app_id,
        api_key=first.api_key,
        algolia_agent=next((item.algolia_agent for item in compatible if item.algolia_agent), None),
        indices=tuple(dict.fromkeys(name for item in compatible for name in item.indices)),
        facet_filters=tuple(
            dict.fromkeys(value for item in compatible for value in item.facet_filters)
        ),
        session_headers=next(
            (item.session_headers for item in compatible if item.session_headers), ()
        ),
    )
