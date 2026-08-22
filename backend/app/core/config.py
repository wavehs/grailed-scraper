"""Typed runtime configuration loaded from the repository-level ``.env`` file."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_URL = f"sqlite+aiosqlite:///{(PROJECT_ROOT / 'data' / 'grailed.db').as_posix()}"


class Settings(BaseSettings):
    """Validated settings for the live Grailed parser."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Grailed Liquidity Analyzer"
    environment: Literal["development", "test", "production"] = "development"
    revision: str | None = None
    backend_bind_host: str = "127.0.0.1"
    frontend_bind_host: str = "127.0.0.1"
    source_mode: Literal["live"] = "live"
    database_url: str = DEFAULT_DATABASE_URL
    sqlite_busy_timeout_ms: int = 5_000
    log_level: str = "INFO"
    data_directory: Path = PROJECT_ROOT / "data"
    log_directory: Path = PROJECT_ROOT / "data" / "logs"
    requests_per_minute: int = 90
    max_concurrent_requests: int = 3
    proxy_url: str | None = None
    proxy_list_browser: list[str] | str = []
    proxy_list_http: list[str] | str = []
    fetch_tier_preferred: Literal["T1", "T2", "T3"] = "T1"
    fetch_tier_allow_browser: bool = True
    fetch_tier_allow_dom: bool = True
    algolia_hits_per_page: int = 200
    algolia_multiquery_batch_size: int = 8
    algolia_pagination_strategy: Literal["auto", "browse", "keyset", "range_split"] = "auto"
    algolia_attributes_mode: Literal["full", "lean"] = "full"
    parser_request_timeout_s: float = 15.0
    parser_max_retries: int = 3
    parser_request_delay_ms: int = 400
    parser_max_concurrency: int = 1
    parser_max_requests_per_run: int = 800
    parser_max_items_per_brand: int = 500
    identity_image_requests_per_run: int = Field(default=100, ge=0, le=100)
    parser_progress_interval_s: float = 2.0
    browser_max_pages: int = 2
    browser_restart_every_requests: int = 300
    browser_restart_every_minutes: int = 20
    browser_use_raw_fallback: bool = False
    discovery_ttl_hours: int = 12
    discovery_sample_size: int = 200
    discovery_page_timeout_s: float = 45.0
    proxy_enabled: bool = False
    proxy_allow_direct_fallback: bool = True
    proxy_rotation_mode: Literal["round_robin", "random", "weighted"] = "weighted"
    cors_origins: list[str] = ["http://127.0.0.1:3000", "http://localhost:3000"]
    parser_mode: Literal["delta", "full"] = "delta"
    parser_full_refresh_days: int = 7
    parser_refresh_active_enabled: bool = True
    parser_refresh_active_limit: int | None = Field(default=None, ge=1)
    parser_removed_confirm_hours: int = 48
    parser_watermark_overlap_hours: int = 2
    quality_price_outlier_mad_k: float = 6.0
    quality_filter_replicas: bool = True
    quality_lot_price_multiplier: float = 1.5
    fx_provider: Literal["static"] = "static"
    store_seller_identity: Literal["none", "hashed", "plain"] = "hashed"
    seller_identity_salt: str | None = None
    live_compliance_acknowledged: bool = False
    raw_data_retention_days: int = 90
    backup_retention_days: int = 30

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed_levels:
            msg = f"Unsupported log level: {value}"
            raise ValueError(msg)
        return normalized

    @field_validator(
        "requests_per_minute",
        "max_concurrent_requests",
        "parser_request_timeout_s",
        "parser_request_delay_ms",
        "parser_max_concurrency",
        "parser_max_requests_per_run",
        "parser_max_items_per_brand",
        "parser_progress_interval_s",
        "browser_max_pages",
        "algolia_hits_per_page",
        "algolia_multiquery_batch_size",
        "browser_restart_every_requests",
        "browser_restart_every_minutes",
        "discovery_ttl_hours",
        "discovery_sample_size",
        "discovery_page_timeout_s",
        "parser_full_refresh_days",
        "parser_removed_confirm_hours",
        "parser_watermark_overlap_hours",
        "quality_price_outlier_mad_k",
        "quality_lot_price_multiplier",
        "raw_data_retention_days",
        "backup_retention_days",
        "sqlite_busy_timeout_ms",
    )
    @classmethod
    def require_positive_limit(cls, value: int | float) -> int | float:
        if value < 1:
            msg = "Must be at least 1"
            raise ValueError(msg)
        return value

    @field_validator("algolia_multiquery_batch_size")
    @classmethod
    def cap_multiquery_batch(cls, value: int) -> int:
        if value > 8:
            raise ValueError("Algolia multi-query supports at most 8 sub-queries")
        return value

    @field_validator("requests_per_minute")
    @classmethod
    def cap_requests_per_minute(cls, value: int) -> int:
        if value > 90:
            raise ValueError("Compliance limit is 90 requests per minute")
        return value

    @field_validator("max_concurrent_requests", "parser_max_concurrency")
    @classmethod
    def cap_concurrency(cls, value: int) -> int:
        if value > 3:
            raise ValueError("Compliance limit is 3 concurrent requests")
        return value

    @field_validator("parser_progress_interval_s")
    @classmethod
    def cap_progress_interval(cls, value: float) -> float:
        if value > 2:
            raise ValueError("Parser progress must be persisted at least every 2 seconds")
        return value

    @field_validator("proxy_list_browser", "proxy_list_http", mode="before")
    @classmethod
    def parse_proxy_list(cls, value: Any) -> list[str]:
        """Accept JSON arrays and the convenient comma-separated env form."""

        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                decoded = value.split(",")
            if not isinstance(decoded, list):
                raise ValueError("Proxy list must be a JSON array or comma-separated URLs")
            return [str(item).strip() for item in decoded if str(item).strip()]
        raise ValueError("Proxy list must contain proxy URLs")

    def proxy_pool(self, kind: Literal["http", "browser"]) -> list[str]:
        """Return the configured pool, retaining the legacy single-proxy setting."""

        configured = self.proxy_list_http if kind == "http" else self.proxy_list_browser
        if configured:
            return _proxy_values(configured)
        if self.proxy_url:
            return [self.proxy_url]
        other_pool = self.proxy_list_browser if kind == "http" else self.proxy_list_http
        return _proxy_values(other_pool)


def _proxy_values(value: list[str] | str) -> list[str]:
    """Narrow validator-normalized proxy settings for static type checking."""

    return value if isinstance(value, list) else [value]


@lru_cache
def get_settings() -> Settings:
    """Return one immutable settings instance for the process lifetime."""

    return Settings()
