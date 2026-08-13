"""Validated, source-agnostic listing payloads and lifecycle helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ListingStatus = Literal["active", "sold", "removed_pending", "removed"]
FetchTier = Literal["T0", "T1", "T2", "T3"]


class ListingData(BaseModel):
    """Normalized listing ready for idempotent persistence."""

    model_config = ConfigDict(extra="forbid")

    source: str = "grailed"
    grailed_id: int = Field(gt=0)
    status: ListingStatus
    url: str
    title: str = Field(min_length=1)
    description: str | None = None
    brand_name_raw: str = Field(min_length=1)
    brand_slug: str | None = None
    brand_id: int | None = Field(default=None, gt=0)
    category: str | None = None
    subcategory: str | None = None
    size_raw: str | None = None
    size_normalized: str | None = None
    condition_raw: str | None = None
    condition: str | None = None
    price: Decimal = Field(gt=0)
    price_original: Decimal | None = Field(default=None, gt=0)
    currency_original: str = Field(min_length=3, max_length=3)
    fx_rate: Decimal | None = Field(default=None, gt=0)
    sold_price: Decimal | None = Field(default=None, gt=0)
    likes_count: int = Field(default=0, ge=0)
    created_at: datetime | None = None
    sold_at: datetime | None = None
    sold_at_is_estimated: bool = False
    updated_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    removed_checked_at: datetime | None = None
    days_on_market: int | None = Field(default=None, ge=0)
    cover_photo_url: str | None = None
    photo_urls: list[str] = Field(default_factory=list)
    photo_count: int = Field(default=0, ge=0)
    seller_identity: str | None = None
    seller_identity_mode: Literal["none", "hashed", "plain"] = "none"
    seller_country: str | None = Field(default=None, min_length=2, max_length=2)
    quality_flags: list[str] = Field(default_factory=list)
    fetch_tier: FetchTier
    parser_run_id: int = Field(gt=0)
    raw_json: dict[str, Any]
    raw_json_purged_at: datetime | None = None
    schema_version: int = Field(ge=1)

    @field_validator("price", "price_original", "fx_rate", "sold_price", mode="before")
    @classmethod
    def require_decimal_money(cls, value: Any) -> Any:
        """Reject floats so monetary values remain exact from normalization onward."""

        if isinstance(value, float):
            raise ValueError("Monetary values must be Decimal, never float")
        return value

    @field_validator("currency_original", "seller_country")
    @classmethod
    def normalize_uppercase_codes(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


def mark_removed_pending(
    status: ListingStatus, checked_at: datetime
) -> tuple[ListingStatus, datetime | None]:
    """Mark only an active listing as temporarily missing, never as sold."""

    if status == "active":
        return "removed_pending", checked_at
    return status, None


def resolve_removed_pending(
    status: ListingStatus,
    checked_at: datetime | None,
    now: datetime,
    *,
    grace_period: timedelta = timedelta(hours=48),
) -> ListingStatus:
    """Resolve a missing listing only after its 48-hour grace period."""

    if status == "removed_pending" and checked_at is not None and now - checked_at >= grace_period:
        return "removed"
    return status
