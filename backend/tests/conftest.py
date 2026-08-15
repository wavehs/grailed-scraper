"""Pytest configuration and Windows-safe cleanup fixtures."""

from __future__ import annotations

import gc
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _cleanup_file_handles() -> Generator[None, None, None]:
    yield
    gc.collect()
