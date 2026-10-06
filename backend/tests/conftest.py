"""Pytest configuration and Windows-safe cleanup fixtures."""

from __future__ import annotations

import gc
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _isolate_grouping_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user's local runtime selection must not redirect provider contract tests."""
    monkeypatch.setenv("APP_AI_GROUPING_PROVIDER", "gemini")


@pytest.fixture(autouse=True)
def _cleanup_file_handles() -> Generator[None, None, None]:
    yield
    gc.collect()
