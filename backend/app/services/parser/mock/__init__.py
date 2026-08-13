"""Deterministic, offline T0 source primitives used by development and tests."""

from app.services.parser.mock.fake_algolia_server import (
    FakeAlgoliaScenario,
    create_fake_algolia_app,
)
from app.services.parser.mock.generator import MockBrand, MockCatalog

__all__ = ["FakeAlgoliaScenario", "MockBrand", "MockCatalog", "create_fake_algolia_app"]
