"""Canonical Algolia parameter serialization."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from app.services.sources.grailed.algolia.models import AlgoliaQuery

BASE_RESPONSE_FIELDS = (
    "hits",
    "nbHits",
    "page",
    "nbPages",
    "hitsPerPage",
    "exhaustiveNbHits",
    "facets",
    "cursor",
    "queryID",
)


def build_params(query: AlgoliaQuery) -> str:
    """Encode nested filters as compact JSON and Unicode as UTF-8 percent escapes."""

    params: dict[str, Any] = {
        "query": query.query,
        "hitsPerPage": query.hits_per_page,
        "page": query.page,
        "attributesToHighlight": _json([]),
        "attributesToSnippet": _json([]),
        "getRankingInfo": "false",
        "analytics": "false",
        "clickAnalytics": "false",
        "enableABTest": "false",
        "responseFields": _json(BASE_RESPONSE_FIELDS),
        "attributesToRetrieve": _json(query.attributes_to_retrieve),
    }
    if query.filters is not None:
        params["filters"] = query.filters
    if query.facet_filters:
        params["facetFilters"] = _json(query.facet_filters)
    if query.numeric_filters:
        params["numericFilters"] = _json(query.numeric_filters)
    if query.facets:
        params["facets"] = _json(query.facets)
    params.update(query.extra)
    return urlencode(params)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
