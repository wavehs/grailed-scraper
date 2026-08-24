"""Convert an untrusted Grailed hit into the strict ListingData contract."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog
from dateutil.parser import isoparse  # type: ignore[import-untyped]
from pydantic import ValidationError

from app.core.config import Settings
from app.core.privacy import seller_identity
from app.domain.listings import FetchTier, ListingData, ListingStatus, to_utc_datetime
from app.services.normalization.fx import FxRateProvider, StaticFxRateProvider
from app.services.normalization.mapping import SourceMappingConfig
from app.services.sources.base.models import RawHit

_CENT = Decimal("0.01")
_LETTER_SIZES = {
    "XXS": "XXS",
    "XS": "XS",
    "S": "S",
    "SMALL": "S",
    "M": "M",
    "MEDIUM": "M",
    "L": "L",
    "LARGE": "L",
    "XL": "XL",
    "XXL": "XXL",
    "2XL": "XXL",
    "OS": "OS",
    "ONE SIZE": "OS",
    "ONE SIZE FITS ALL": "OS",
}
_FOOTWEAR_EU_TO_US = {
    35: Decimal("3"),
    36: Decimal("4"),
    37: Decimal("5"),
    38: Decimal("6"),
    39: Decimal("7"),
    40: Decimal("7.5"),
    41: Decimal("8.5"),
    42: Decimal("9"),
    43: Decimal("10"),
    44: Decimal("11"),
    45: Decimal("12"),
    46: Decimal("13"),
    47: Decimal("14"),
    48: Decimal("15"),
}


@dataclass(frozen=True, slots=True)
class NormalizationContext:
    status: ListingStatus
    parser_run_id: int
    observed_at: datetime
    brand_id: int | None = None
    schema_version: int | None = None
    fetch_tier: FetchTier | None = None


@dataclass(frozen=True, slots=True)
class NormalizationFailure:
    code: str
    field: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    listing: ListingData | None
    failures: tuple[NormalizationFailure, ...] = ()

    @property
    def valid(self) -> bool:
        return self.listing is not None


class ListingNormalizer:
    def __init__(
        self,
        mapping: SourceMappingConfig,
        fx_rates: FxRateProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._mapping = mapping
        self._fx_rates = fx_rates or StaticFxRateProvider()
        self._settings = settings or Settings()
        self._conditions = {
            source_value.casefold(): normalized
            for normalized, source_value in mapping.conditions.items()
        }

    async def normalize(self, hit: RawHit, context: NormalizationContext) -> NormalizationResult:
        payload = hit.payload
        failures: list[NormalizationFailure] = []
        grailed_id = _positive_int(self._mapping.value(payload, "grailed_id"))
        title = _text(self._mapping.value(payload, "title"))
        brand_name = _text(self._mapping.value(payload, "brand_name"))
        price_original = _money(self._mapping.value(payload, "price_float"))
        if grailed_id is None:
            failures.append(NormalizationFailure("invalid", "grailed_id", "missing or invalid id"))
        if title is None:
            failures.append(NormalizationFailure("invalid", "title", "missing title"))
        if brand_name is None:
            failures.append(NormalizationFailure("invalid", "brand_name", "missing designers"))
        if price_original is None or price_original <= 0:
            failures.append(NormalizationFailure("invalid", "price", "price must be positive"))
        if failures:
            _log_rejection(hit, failures)
            return NormalizationResult(None, tuple(failures))
        assert grailed_id is not None
        assert title is not None
        assert brand_name is not None
        assert price_original is not None

        currency = (_text(self._mapping.value(payload, "currency")) or "USD").upper()
        created_at = _timestamp(self._mapping.value(payload, "created_at"))
        updated_at = _timestamp(self._mapping.value(payload, "updated_at"))
        sold_at = _timestamp(self._mapping.value(payload, "sold_at"))
        sold_at_is_estimated = False
        if context.status == "sold" and sold_at is None:
            sold_at = updated_at or context.observed_at
            sold_at_is_estimated = True
        rate_date = (sold_at or created_at or context.observed_at).date()
        fx_rate = await self._fx_rates.rate_to_usd(currency, rate_date)
        if currency != "USD" and fx_rate is None:
            missing_rate = NormalizationFailure(
                "missing_fx_rate", "currency", f"{currency}:{rate_date}"
            )
            _log_rejection(hit, [missing_rate])
            return NormalizationResult(None, (missing_rate,))
        price = (
            price_original
            if currency == "USD"
            else (price_original * (fx_rate or Decimal(1))).quantize(_CENT, ROUND_HALF_UP)
        )
        sold_price_original = _money(self._mapping.value(payload, "sold_price"))
        sold_price = None
        if context.status == "sold":
            sold_price = sold_price_original or price_original
            if currency != "USD":
                sold_price = (sold_price * (fx_rate or Decimal(1))).quantize(_CENT, ROUND_HALF_UP)
        size_raw = _text(self._mapping.value(payload, "size"))
        condition_raw = _text(self._mapping.value(payload, "condition"))
        photo_urls = _string_list(self._mapping.value(payload, "photo_urls"))
        cover = _text(self._mapping.value(payload, "cover_photo_url"))
        cover_asset_key = _asset_key(
            _text(self._mapping.value(payload, "cover_asset_url")) or cover
        )
        if cover and cover not in photo_urls:
            photo_urls.insert(0, cover)
        first_seen = to_utc_datetime(context.observed_at) or context.observed_at
        normalized_created = created_at or first_seen
        days_on_market = None
        if sold_at is not None and not sold_at_is_estimated:
            days_on_market = max((sold_at - normalized_created).days, 0)
        url = _text(self._mapping.value(payload, "url")) or (
            f"https://www.grailed.com/listings/{grailed_id}"
        )
        try:
            listing = ListingData(
                source=self._mapping.source,
                grailed_id=grailed_id,
                status=context.status,
                url=url,
                title=title,
                description=_text(self._mapping.value(payload, "description")),
                brand_name_raw=brand_name,
                brand_slug=_text(self._mapping.value(payload, "brand_slug")),
                brand_id=context.brand_id,
                category=_category(self._mapping.value(payload, "category")),
                subcategory=_category(self._mapping.value(payload, "subcategory")),
                size_raw=size_raw,
                size_normalized=normalize_size(
                    size_raw, _category(self._mapping.value(payload, "category"))
                ),
                condition_raw=condition_raw,
                condition=(
                    self._conditions.get(condition_raw.casefold()) if condition_raw else None
                ),
                color=_text(self._mapping.value(payload, "color")),
                source_product_id=_positive_int(self._mapping.value(payload, "product_id")),
                source_sku_id=_positive_int(self._mapping.value(payload, "sku_id")),
                source_repost_id=_positive_int(self._mapping.value(payload, "repost_id")),
                price=price,
                price_original=price_original if currency != "USD" else None,
                currency_original=currency,
                fx_rate=fx_rate if currency != "USD" else None,
                sold_price=sold_price,
                likes_count=_nonnegative_int(self._mapping.value(payload, "likes_count")),
                created_at=normalized_created,
                sold_at=sold_at,
                sold_at_is_estimated=sold_at_is_estimated,
                updated_at=updated_at,
                first_seen_at=first_seen,
                last_seen_at=first_seen,
                days_on_market=days_on_market,
                cover_photo_url=cover,
                cover_asset_key=cover_asset_key,
                photo_urls=photo_urls,
                photo_count=max(
                    _nonnegative_int(self._mapping.value(payload, "photo_count")),
                    len(photo_urls),
                ),
                seller_identity=seller_identity(
                    self._mapping.value(payload, "seller_username"), self._settings
                ),
                seller_identity_mode=self._settings.store_seller_identity,
                seller_country=_country(self._mapping.value(payload, "location")),
                quality_flags=[],
                fetch_tier=context.fetch_tier or hit.fetch_tier,
                parser_run_id=context.parser_run_id,
                raw_json=_sanitize_raw_json(payload),
                schema_version=context.schema_version or self._mapping.schema_version,
            )
        except ValidationError as exc:
            failure = NormalizationFailure("invalid", detail=str(exc))
            _log_rejection(hit, [failure])
            return NormalizationResult(None, (failure,))
        return NormalizationResult(listing)


def normalize_size(value: str | None, category: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value.strip().upper())
    if cleaned in _LETTER_SIZES:
        return _LETTER_SIZES[cleaned]
    category_name = (category or "").casefold()
    number_match = re.search(r"\d+(?:\.5)?", cleaned)
    if number_match is None:
        return None
    number = Decimal(number_match.group())
    if "bottom" in category_name or "pant" in category_name:
        waist = int(number)
        return f"W{waist}" if 28 <= waist <= 40 else None
    if "foot" in category_name or "shoe" in category_name or "sneaker" in category_name:
        if "EU" in cleaned:
            us = _FOOTWEAR_EU_TO_US.get(int(number))
        elif "UK" in cleaned:
            us = number + Decimal(1)
        elif "JP" in cleaned or "CM" in cleaned:
            us = number - Decimal(18)
        else:
            us = number
        if us is None or not Decimal(3) <= us <= Decimal(18):
            return None
        return f"US {format(us.normalize(), 'f')}"
    return None


def _money(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).quantize(_CENT, ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return to_utc_datetime(value)
    if isinstance(value, (int, float, Decimal)):
        numeric = Decimal(str(value))
        if numeric <= 0:
            return None
        if abs(numeric) > Decimal("100000000000"):
            numeric /= Decimal(1000)
        try:
            return datetime.fromtimestamp(float(numeric), tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return to_utc_datetime(isoparse(value))
        except (TypeError, ValueError, OverflowError):
            try:
                return _timestamp(Decimal(value))
            except InvalidOperation:
                return None
    return None


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    result = re.sub(r"\s+", " ", str(value)).strip()
    return result or None


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _nonnegative_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_text(part) for part in value) if item is not None]


def _asset_key(value: str | None) -> str | None:
    if value is None:
        return None
    parts = urlsplit(value)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return None
    canonical = urlunsplit(
        (parts.scheme.casefold(), parts.hostname.casefold(), parts.path, "", "")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _category(value: Any) -> str | None:
    if isinstance(value, list):
        return _text(value[-1]) if value else None
    return _text(value)


def _country(value: Any) -> str | None:
    text = _text(value)
    return text.upper() if text and len(text) == 2 else None


def _log_rejection(hit: RawHit, failures: list[NormalizationFailure]) -> None:
    structlog.get_logger(__name__).warning(
        "listing_normalization_rejected",
        object_id=hit.object_id,
        reasons=[failure.code for failure in failures],
        fields=[failure.field for failure in failures if failure.field],
    )


def _sanitize_raw_json(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = key.casefold()
            if _sensitive_raw_key(normalized_key, path):
                continue
            if normalized_key == "location":
                country = _country(item)
                if country is not None:
                    sanitized[key] = country
                continue
            sanitized[key] = _sanitize_raw_json(item, (*path, normalized_key))
        return sanitized
    if isinstance(value, list):
        return [_sanitize_raw_json(item, path) for item in value]
    return value


def _sensitive_raw_key(key: str, path: tuple[str, ...]) -> bool:
    return key in {
        "username",
        "seller_username",
        "seller_id",
        "email",
        "latitude",
        "longitude",
        "coordinates",
        "address",
        "city",
        "state",
        "postal_code",
        "postcode",
        "zip",
        "zipcode",
    } or (key == "id" and ("seller" in path or "user" in path))
