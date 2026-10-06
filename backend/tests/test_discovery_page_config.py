"""Parsing of Grailed's public page configuration (source-independent)."""

from __future__ import annotations

import json

from app.services.sources.grailed.discovery.page_config import parse_public_config


def _page(config: object) -> str:
    return f"<html><body><script>window.PUBLIC_CONFIG={json.dumps(config)};</script></body></html>"


def test_public_config_yields_search_credentials_and_listing_indices() -> None:
    seed = parse_public_config(
        _page(
            {
                "algolia": {
                    "app_id": "ABCDEFGH12",
                    "public_search_key": "0123456789abcdef0123456789abcdef",
                    "indexes": {
                        "listings": [
                            {"name": "heat desc", "value": "Listing_by_heat_production"},
                            {"name": "default", "value": "Listing_production"},
                        ],
                        "designers": [{"name": "designer", "value": "Designer_production"}],
                    },
                }
            }
        )
    )

    assert seed is not None
    assert seed.app_id == "ABCDEFGH12"
    assert seed.api_key == "0123456789abcdef0123456789abcdef"
    assert seed.method == "page_config"
    assert seed.indices == ("Listing_by_heat_production", "Listing_production")


def test_missing_or_malformed_config_is_rejected() -> None:
    assert parse_public_config("<html></html>") is None
    assert parse_public_config("<script>window.PUBLIC_CONFIG={broken};</script>") is None
    bad = {"algolia": {"app_id": "bad id", "public_search_key": "x"}}
    assert parse_public_config(_page(bad)) is None
