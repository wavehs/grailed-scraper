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


def test_scrapling_is_pinned() -> None:
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")

    assert "scrapling[fetchers]==0.4.11" in requirements


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


def test_proxy_pools_accept_environment_friendly_values() -> None:
    settings = Settings(
        proxy_list_http="http://one:1,socks5://two:2",
        proxy_list_browser='["https://three:3"]',
    )

    assert settings.proxy_pool("http") == ["http://one:1", "socks5://two:2"]
    assert settings.proxy_pool("browser") == ["https://three:3"]


def test_fetching_settings_keep_safe_limits() -> None:
    settings = Settings()
    assert settings.fetch_tier_allow_dom is True
    assert settings.algolia_hits_per_page == 200
    assert settings.algolia_multiquery_batch_size == 8
    with pytest.raises(ValidationError, match="at most 8"):
        Settings(algolia_multiquery_batch_size=9)
