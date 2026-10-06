"""Checks for the reproducible development baseline."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app import __version__
from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_source_mode_is_live_only() -> None:
    contents = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "APP_SOURCE_MODE=live" in contents


def test_backend_package_exposes_a_version() -> None:
    assert __version__ == "1.0.0"


def test_http_stack_is_pinned_without_browsers() -> None:
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")

    assert "curl_cffi==0.16.3" in requirements
    for package in ("scrapling", "camoufox", "playwright", "patchright", "pywebview"):
        assert package not in requirements.casefold()


def test_reproducible_runtime_contract() -> None:
    runtime = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
    development = (ROOT / "backend" / "requirements-dev.txt").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.11.9"
    assert (ROOT / ".nvmrc").read_text(encoding="utf-8").strip() == "20.19.5"
    assert "APScheduler" not in runtime and "apscheduler" not in runtime
    assert "pytest==" not in runtime
    assert "-r requirements.txt" in development
    assert '"packageManager": "pnpm@9.15.9"' in frontend
    assert "pip-audit -r backend/requirements-dev.txt" in ci
    assert "pnpm audit --audit-level high" in ci
    assert ci.index("alembic upgrade head") < ci.index("- run: pytest")


def test_non_ascii_powershell_scripts_have_a_utf8_bom() -> None:
    # Windows PowerShell 5.1 reads BOM-less scripts as the ANSI code page, which turns
    # Cyrillic UTF-8 bytes into smart quotes and breaks parsing.
    for script in (ROOT / "scripts").glob("*.ps1"):
        data = script.read_bytes()
        if not data.isascii():
            assert data.startswith(b"\xef\xbb\xbf"), script.name


def test_fetching_settings_keep_safe_limits() -> None:
    settings = Settings()
    assert settings.algolia_hits_per_page == 1_000
    assert settings.algolia_multiquery_batch_size == 8
    assert settings.requests_per_minute <= 90
    assert settings.max_concurrent_requests <= 3
    with pytest.raises(ValidationError, match="at most 1000"):
        Settings(algolia_hits_per_page=1_001)
    with pytest.raises(ValidationError, match="at most 8"):
        Settings(algolia_multiquery_batch_size=9)
