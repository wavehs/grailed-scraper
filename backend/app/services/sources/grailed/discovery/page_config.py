"""Read Grailed's public Algolia search configuration from a regular HTML page."""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.sources.grailed.discovery.models import DiscoverySeed
from app.services.transport.protocols import HttpTransport

CONFIG_URL = "https://www.grailed.com/shop"
_CONFIG = re.compile(r"window\.PUBLIC_CONFIG\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)
_APP_ID = re.compile(r"^[A-Z0-9]{8,12}$")
_KEY = re.compile(r"^[A-Za-z0-9]{16,128}$")


def parse_public_config(html: str) -> DiscoverySeed | None:
    """Extract app id, public search key and index names from ``window.PUBLIC_CONFIG``."""

    match = _CONFIG.search(html)
    if match is None:
        return None
    try:
        config = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    algolia = config.get("algolia") if isinstance(config, dict) else None
    if not isinstance(algolia, dict):
        return None
    app_id = algolia.get("app_id")
    api_key = algolia.get("public_search_key")
    if not isinstance(app_id, str) or not isinstance(api_key, str):
        return None
    if not _APP_ID.fullmatch(app_id) or not _KEY.fullmatch(api_key):
        return None
    return DiscoverySeed(
        app_id=app_id,
        api_key=api_key,
        indices=tuple(dict.fromkeys(_listing_indices(algolia.get("indexes")))),
        method="page_config",
    )


async def discover_from_page_config(transport: HttpTransport) -> DiscoverySeed | None:
    response = await transport.request("GET", CONFIG_URL)
    if response.status_code != 200:
        return None
    return parse_public_config(response.text)


def _listing_indices(indexes: Any) -> list[str]:
    if not isinstance(indexes, dict):
        return []
    names: list[str] = []
    for group in ("listings", "sold"):
        for item in indexes.get(group) or []:
            value = item.get("value") if isinstance(item, dict) else None
            if isinstance(value, str):
                names.append(value)
    return names
