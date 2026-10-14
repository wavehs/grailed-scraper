"""Pytest configuration and file-handle cleanup fixtures."""

from __future__ import annotations

import gc
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# Keep the app lifespan away from the developer's real database, lock and logs.
_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="grailed-tests-"))
os.environ["APP_DATA_DIRECTORY"] = str(_RUNTIME_DIR)
os.environ["APP_LOG_DIRECTORY"] = str(_RUNTIME_DIR / "logs")
os.environ["APP_DATABASE_URL"] = f"sqlite+aiosqlite:///{(_RUNTIME_DIR / 'test.db').as_posix()}"
os.environ["APP_LIVE_COMPLIANCE_ACKNOWLEDGED"] = "true"
# Collection tuning in the local .env (faster, deeper) must not change what the tests assert.
for _name, _default in (
    ("APP_REQUESTS_PER_MINUTE", "90"),
    ("APP_ALGOLIA_HITS_PER_PAGE", "1000"),
    ("APP_PARSER_MAX_REQUESTS_PER_RUN", "50000"),
    ("APP_PARSER_MAX_CONCURRENCY", "1"),
    ("APP_SOLD_HISTORY_DAYS", "365"),
):
    os.environ[_name] = _default


@pytest.fixture(autouse=True)
def _cleanup_file_handles() -> Generator[None, None, None]:
    yield
    gc.collect()
