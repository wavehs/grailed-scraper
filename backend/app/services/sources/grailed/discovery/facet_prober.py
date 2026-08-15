"""Detect canonical brand/category facets from an Algolia index."""

from __future__ import annotations

from urllib.parse import urlencode

from app.services.sources.grailed.discovery.client import DiscoveryAlgoliaClient

BRAND_CANDIDATES = ("designers.name", "designer_names", "brand")
CATEGORY_CANDIDATES = ("category_path", "category", "department")


async def probe_facets(
    client: DiscoveryAlgoliaClient, index: str
) -> tuple[str | None, str | None, tuple[str, ...]]:
    params = urlencode({"query": "", "facets": '["*"]', "maxValuesPerFacet": 100, "hitsPerPage": 0})
    payload = await client.json("POST", client.index_path(index), json_body={"params": params})
    facets = payload.get("facets", {}) if payload else {}
    names = tuple(facets) if isinstance(facets, dict) else ()
    brand = next((candidate for candidate in BRAND_CANDIDATES if candidate in names), None)
    category = next((candidate for candidate in CATEGORY_CANDIDATES if candidate in names), None)
    if brand:
        await client.json(
            "POST",
            client.index_path(index, f"facets/{brand}/query"),
            json_body={"facetQuery": ""},
        )
    return brand, category, names
