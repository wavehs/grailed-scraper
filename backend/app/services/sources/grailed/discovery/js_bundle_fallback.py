"""Credential candidate extraction from Grailed's public JavaScript bundles."""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urljoin

from app.services.sources.grailed.discovery.models import DiscoverySeed
from app.services.transport.protocols import HttpTransport

APP_PATTERN = re.compile(
    r"""["']?(?:appId|applicationId|ALGOLIA_APP_ID)["']?\s*[:=]\s*["']([A-Z0-9]{8,12})["']"""
)
KEY_PATTERN = re.compile(
    r"""["']?(?:apiKey|searchApiKey|ALGOLIA_[A-Z_]*KEY)["']?\s*[:=]\s*["']([a-f0-9]{32})["']""",
    re.IGNORECASE,
)
INDEX_PATTERN = re.compile(r"""["']([A-Za-z_]+_(?:production|prod))["']""")
SCRIPT_PATTERN = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


def extract_bundle_candidates(text: str) -> list[DiscoverySeed]:
    apps = tuple(dict.fromkeys(APP_PATTERN.findall(text)))
    keys = tuple(dict.fromkeys(KEY_PATTERN.findall(text)))
    indices = tuple(dict.fromkeys(INDEX_PATTERN.findall(text)))
    return [
        DiscoverySeed(app_id=app, api_key=key, indices=indices, method="bundle")
        for app in apps
        for key in keys
    ]


async def discover_from_bundles(
    transport: HttpTransport, *, page_url: str = "https://www.grailed.com/"
) -> list[DiscoverySeed]:
    response = await transport.request("GET", page_url)
    if response.status_code != 200:
        return []
    documents = [response.text]
    for source in tuple(dict.fromkeys(SCRIPT_PATTERN.findall(response.text))):
        script = await transport.request("GET", urljoin(page_url, source))
        if script.status_code == 200:
            documents.append(script.text)
    candidates: list[DiscoverySeed] = []
    shared_indices = tuple(
        dict.fromkeys(name for document in documents for name in INDEX_PATTERN.findall(document))
    )
    for document in documents:
        candidates.extend(
            replace(candidate, indices=shared_indices)
            for candidate in extract_bundle_candidates(document)
        )
    return list({(item.app_id, item.api_key): item for item in candidates}.values())
