"""Validate discovered Algolia indices and their search limits."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from app.services.sources.grailed.discovery.client import (
    DiscoveryAlgoliaClient,
    DiscoveryHttpError,
)
from app.services.sources.grailed.discovery.models import IndexProbe


async def probe_indices(
    client: DiscoveryAlgoliaClient, candidates: tuple[str, ...]
) -> tuple[IndexProbe, ...]:
    probes: list[IndexProbe] = []
    for name in dict.fromkeys(candidates):
        try:
            first = await _query(client, name, {"query": "", "hitsPerPage": 2, "page": 0})
        except DiscoveryHttpError as exc:
            if exc.status_code == 404:
                continue
            raise
        nb_hits = _int(first.get("nbHits"), 0)
        max_hits = await _max_hits_per_page(client, name)
        pagination_limit = await _pagination_limit(client, name, nb_hits)
        probes.append(
            IndexProbe(
                name=name,
                nb_hits=nb_hits,
                pagination_limit=pagination_limit,
                max_hits_per_page=max_hits,
                # Index names are not proof of stable keyset ordering.
                sort_field=None,
            )
        )
    return tuple(probes)


async def _max_hits_per_page(client: DiscoveryAlgoliaClient, index: str) -> int:
    for requested in (1000, 500, 250, 100):
        payload = await _query(client, index, {"query": "", "hitsPerPage": requested, "page": 0})
        accepted = _int(payload.get("hitsPerPage"), 0)
        if accepted > 0:
            return min(requested, accepted)
    return 20


async def _pagination_limit(client: DiscoveryAlgoliaClient, index: str, nb_hits: int) -> int | None:
    if nb_hits <= 0:
        return None
    low, high = 0, min(nb_hits - 1, 9_999)
    last_non_empty = -1
    while low <= high:
        midpoint = (low + high) // 2
        payload = await _query(client, index, {"query": "", "hitsPerPage": 1, "page": midpoint})
        if payload.get("hits"):
            last_non_empty = midpoint
            low = midpoint + 1
        else:
            high = midpoint - 1
    return last_non_empty + 1 if last_non_empty >= 0 else None


async def _query(
    client: DiscoveryAlgoliaClient, index: str, params: dict[str, Any]
) -> dict[str, Any]:
    payload = await client.json(
        "POST", client.index_path(index), json_body={"params": urlencode(params)}
    )
    return payload or {}


def _int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default
