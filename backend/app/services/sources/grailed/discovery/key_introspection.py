"""Read the public search key's own ACL and limits when Algolia permits it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.services.sources.grailed.discovery.client import (
    DiscoveryAlgoliaClient,
    DiscoveryHttpError,
)
from app.services.sources.grailed.discovery.models import KeyCapabilities


async def introspect_key(client: DiscoveryAlgoliaClient, api_key: str) -> KeyCapabilities:
    try:
        payload = await client.json(
            "GET", f"/1/keys/{quote(api_key, safe='')}", allow_forbidden=True
        )
    except DiscoveryHttpError:
        raise
    except Exception:
        raise DiscoveryHttpError(503, "/1/keys/[redacted]") from None
    if payload is None:
        return KeyCapabilities()
    valid_until = _valid_until(payload.get("validUntil", payload.get("validity")))
    return KeyCapabilities(
        acl=tuple(_strings(payload.get("acl"))),
        indexes=tuple(_strings(payload.get("indexes"))),
        valid_until=valid_until,
        max_queries_per_ip_per_hour=_integer(payload.get("maxQueriesPerIPPerHour")),
        max_hits_per_query=_integer(payload.get("maxHitsPerQuery")),
    )


def _valid_until(value: Any) -> datetime | None:
    integer = _integer(value)
    return datetime.fromtimestamp(integer, tz=UTC) if integer is not None and integer > 0 else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
