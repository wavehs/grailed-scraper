"""ASGI-only Algolia substitute for deterministic T0 integration tests."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from math import ceil
from typing import Any, Literal
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.services.parser.mock.generator import MockCatalog

FaultMode = Literal["none", "forbidden", "rate_limited", "server_error", "waf", "slow"]


@dataclass(slots=True)
class FakeAlgoliaScenario:
    """Apply a deterministic fault to the first ``failures`` handled requests."""

    fault: FaultMode = "none"
    failures: int = 0
    slow_delay_s: float = 0.01
    acl: tuple[str, ...] = ("search", "browse")
    pagination_limit: int = 1_000
    max_hits_per_query: int = 1_000
    _requests_seen: int = field(default=0, init=False)

    @property
    def requests_seen(self) -> int:
        return self._requests_seen

    async def response(self) -> Response | None:
        self._requests_seen += 1
        if self.fault == "slow":
            if self._requests_seen <= max(self.failures, 1):
                await asyncio.sleep(self.slow_delay_s)
            return None
        if self._requests_seen > self.failures or self.fault == "none":
            return None
        if self.fault == "forbidden":
            return JSONResponse({"message": "Forbidden (fixture)"}, status_code=403)
        if self.fault == "rate_limited":
            return JSONResponse(
                {"message": "Rate limit reached (fixture)"},
                status_code=429,
                headers={"Retry-After": "1"},
            )
        if self.fault == "server_error":
            return JSONResponse({"message": "Temporary failure (fixture)"}, status_code=503)
        if self.fault == "waf":
            return HTMLResponse("<html><body>Fixture WAF challenge</body></html>", status_code=200)
        return None


def create_fake_algolia_app(
    catalog: MockCatalog | None = None, scenario: FakeAlgoliaScenario | None = None
) -> FastAPI:
    """Create a fresh app so tests cannot leak request counts or fixtures."""

    fixture_catalog = catalog or MockCatalog.generate()
    fixture_scenario = scenario or FakeAlgoliaScenario()
    app = FastAPI(title="Fake Algolia", version=fixture_catalog.version)

    @app.middleware("http")
    async def apply_scenario(request: Request, call_next: Any) -> Response:
        response = await fixture_scenario.response()
        return response if response is not None else await call_next(request)

    @app.post("/1/indexes/*/queries")
    async def multi_query(payload: dict[str, Any]) -> dict[str, Any]:
        requests = payload.get("requests", [])
        if not isinstance(requests, list):
            return {"results": []}
        return {
            "results": [
                _search_response(
                    fixture_catalog,
                    str(item.get("indexName", "")),
                    _parse_params(item.get("params")),
                    pagination_limit=fixture_scenario.pagination_limit,
                    max_hits_per_query=fixture_scenario.max_hits_per_query,
                )
                for item in requests
                if isinstance(item, dict)
            ]
        }

    @app.post("/1/indexes/{index_name}/query")
    async def query(index_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _search_response(
            fixture_catalog,
            index_name,
            _parse_params(payload.get("params")),
            pagination_limit=fixture_scenario.pagination_limit,
            max_hits_per_query=fixture_scenario.max_hits_per_query,
        )

    @app.post("/1/indexes/{index_name}/browse")
    async def browse(index_name: str, payload: dict[str, Any]) -> Any:
        if "browse" not in fixture_scenario.acl:
            return JSONResponse({"message": "Browse forbidden (fixture)"}, status_code=403)
        params = _parse_params(payload.get("params"))
        records = _filtered_records(fixture_catalog.records_for_index(index_name), params)
        page_size = min(max(_as_int(params.get("hitsPerPage"), 200), 1), 1_000)
        offset = _decode_cursor(payload.get("cursor"))
        hits = records[offset : offset + page_size]
        next_offset = offset + len(hits)
        response: dict[str, Any] = {"hits": hits, "nbHits": len(records)}
        if next_offset < len(records):
            response["cursor"] = _encode_cursor(next_offset)
        return response

    @app.post("/1/indexes/{index_name}/facets/{facet_name}/query")
    async def facet_query(
        index_name: str, facet_name: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if facet_name != "designers.name":
            return {"facetHits": [], "exhaustiveFacetsCount": True}
        query_text = str(payload.get("facetQuery", payload.get("query", ""))).casefold()
        counts: dict[str, int] = {}
        for record in fixture_catalog.records_for_index(index_name):
            designer = str(record["designers"][0]["name"])
            if query_text in designer.casefold():
                counts[designer] = counts.get(designer, 0) + 1
        return {
            "facetHits": [
                {"value": name, "highlighted": name, "count": count}
                for name, count in sorted(counts.items())
            ],
            "exhaustiveFacetsCount": True,
        }

    @app.get("/1/keys/{key}")
    async def key_introspection(key: str) -> dict[str, Any]:
        del key
        return {
            "acl": list(fixture_scenario.acl),
            "validUntil": -1,
            "maxQueriesPerIPPerHour": 5_400,
            "maxHitsPerQuery": fixture_scenario.max_hits_per_query,
        }

    return app


def _parse_params(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    parsed: dict[str, Any] = dict(parse_qsl(value, keep_blank_values=True))
    for key in ("facetFilters", "numericFilters"):
        if key in parsed:
            try:
                parsed[key] = json.loads(parsed[key])
            except json.JSONDecodeError:
                parsed[key] = []
    return parsed


def _search_response(
    catalog: MockCatalog,
    index_name: str,
    params: dict[str, Any],
    *,
    pagination_limit: int = 1_000,
    max_hits_per_query: int = 1_000,
) -> dict[str, Any]:
    records = _filtered_records(catalog.records_for_index(index_name), params)
    requested_hits = _as_int(params.get("hitsPerPage"), 20)
    hits_per_page = min(max(requested_hits, 0), max_hits_per_query)
    page = max(_as_int(params.get("page"), 0), 0)
    visible_total = min(len(records), pagination_limit)
    start = page * hits_per_page
    hits = records[start : min(start + hits_per_page, visible_total)]
    return {
        "hits": hits,
        "nbHits": len(records),
        "page": page,
        "nbPages": ceil(visible_total / hits_per_page) if hits_per_page else 0,
        "hitsPerPage": hits_per_page,
        "exhaustiveNbHits": len(records) <= pagination_limit,
        "facets": _facets(records),
        "queryID": "fixture-query-id",
    }


def _filtered_records(
    records: tuple[dict[str, Any], ...], params: dict[str, Any]
) -> list[dict[str, Any]]:
    query = str(params.get("query", "")).casefold()
    result = [
        record
        for record in records
        if not query
        or query in str(record["title"]).casefold()
        or query in str(record["designers"][0]["name"]).casefold()
    ]
    for group in _as_filter_groups(params.get("facetFilters")):
        alternatives = [item for item in group if item.startswith("designers.name:")]
        if alternatives:
            allowed = {item.removeprefix("designers.name:") for item in alternatives}
            result = [record for record in result if record["designers"][0]["name"] in allowed]
    source_filters = params.get("filters")
    if isinstance(source_filters, str) and "objectID:" in source_filters:
        allowed_ids = {
            match.group(1)
            for match in re.finditer(r"objectID:([^\s()]+)", source_filters)
        }
        result = [
            record
            for record in result
            if str(record.get("objectID", record.get("id"))) in allowed_ids
        ]
    for expression in _as_filter_groups(params.get("numericFilters")):
        for item in expression:
            result = _apply_numeric_filter(result, item)
    return result


def _as_filter_groups(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    groups: list[list[str]] = []
    for item in value:
        if isinstance(item, str):
            groups.append([item])
        elif isinstance(item, list):
            groups.append([part for part in item if isinstance(part, str)])
    return groups


def _apply_numeric_filter(records: list[dict[str, Any]], expression: str) -> list[dict[str, Any]]:
    for operator in (">=", "<=", ">", "<", "="):
        if operator not in expression:
            continue
        field, raw_value = (part.strip() for part in expression.split(operator, maxsplit=1))
        try:
            value = int(raw_value)
        except ValueError:
            return records
        candidates = (field, f"{field}_i")
        return [
            record
            for record in records
            if _matches_numeric(record, candidates, operator, value)
        ]
    return records


def _matches_numeric(
    record: dict[str, Any], candidates: tuple[str, str], operator: str, value: int
) -> bool:
    raw = next((record[name] for name in candidates if name in record), None)
    if not isinstance(raw, int):
        return False
    return {
        ">=": raw >= value,
        "<=": raw <= value,
        ">": raw > value,
        "<": raw < value,
        "=": raw == value,
    }[operator]


def _facets(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for record in records:
        designer = str(record["designers"][0]["name"])
        counts[designer] = counts.get(designer, 0) + 1
    return {"designers.name": counts}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: Any) -> int:
    if not isinstance(cursor, str):
        return 0
    try:
        return max(int(base64.urlsafe_b64decode(cursor.encode()).decode()), 0)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return 0
