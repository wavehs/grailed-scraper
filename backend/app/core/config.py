"""Typed runtime configuration loaded from the repository-level ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
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
    algolia_hits_per_page: int = 1_000
    algolia_multiquery_batch_size: int = 8
    algolia_pagination_strategy: Literal["auto", "browse", "keyset", "range_split"] = "auto"
    algolia_attributes_mode: Literal["full", "lean"] = "full"
    parser_request_timeout_s: float = 15.0
    parser_max_retries: int = 3
    parser_max_concurrency: int = 1
    # Safety net against runaway pagination, not a collection budget (~9 h at 90 rpm).
    parser_max_requests_per_run: int = 50_000
    sold_history_days: int = Field(default=365, ge=30, le=3650)
    collect_price_min_usd: int | None = Field(default=None, ge=0)
    collect_price_max_usd: int | None = Field(default=None, ge=1)
    parser_progress_interval_s: float = 2.0
    discovery_ttl_hours: int = 12
    discovery_sample_size: int = 200
    cors_origins: list[str] = ["http://127.0.0.1:3000", "http://localhost:3000"]
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

    @model_validator(mode="after")
    def check_price_band(self) -> Settings:
        low, high = self.collect_price_min_usd, self.collect_price_max_usd
        if low is not None and high is not None and low > high:
            raise ValueError("collect_price_min_usd must not exceed collect_price_max_usd")
        return self

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
        "parser_max_concurrency",
        "parser_max_requests_per_run",
        "parser_progress_interval_s",
        "algolia_hits_per_page",
        "algolia_multiquery_batch_size",
        "discovery_ttl_hours",
        "discovery_sample_size",
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

    @field_validator("algolia_hits_per_page")
    @classmethod
    def cap_hits_per_page(cls, value: int) -> int:
        if value > 1_000:
            raise ValueError("Algolia returns at most 1000 hits per page")
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


@lru_cache
def get_settings() -> Settings:
    """Return one immutable settings instance for the process lifetime."""

    return Settings()
