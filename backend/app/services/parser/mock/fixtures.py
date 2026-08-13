"""Versioned fixture asset discovery and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT
from app.services.parser.mock.generator import DEFAULT_CATALOG_SEED, FIXTURE_VERSION, MockCatalog

FIXTURES_ROOT = PROJECT_ROOT / "data" / "fixtures" / "grailed"


def fixture_directory(version: str = FIXTURE_VERSION) -> Path:
    return FIXTURES_ROOT / version


def load_manifest(version: str = FIXTURE_VERSION) -> dict[str, Any]:
    manifest_path = fixture_directory(version) / "manifest.json"
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Fixture manifest must be an object: {manifest_path}")
    manifest: dict[str, Any] = loaded
    if manifest.get("version") != version:
        raise ValueError(f"Fixture manifest version mismatch: {manifest_path}")
    return manifest


def load_catalog(version: str = FIXTURE_VERSION) -> MockCatalog:
    manifest = load_manifest(version)
    seed = manifest.get("seed")
    if not isinstance(seed, int):
        raise ValueError("Fixture manifest must contain an integer seed")
    return MockCatalog.generate(seed=seed)


def validate_fixture_assets(version: str = FIXTURE_VERSION) -> dict[str, Any]:
    manifest = load_manifest(version)
    catalog = load_catalog(version)
    required_files = ("sample-search.json", "search-page.html", "listing-page.html")
    missing = [name for name in required_files if not (fixture_directory(version) / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing fixture assets: {', '.join(missing)}")
    if catalog.seed != DEFAULT_CATALOG_SEED:
        raise ValueError("Unexpected fixture catalog seed")
    if manifest.get("brands") != 21:
        raise ValueError("Fixture manifest must describe 21 brands")
    return catalog.manifest()
