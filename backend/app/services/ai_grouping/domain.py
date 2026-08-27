"""Pure safety and identity helpers for AI-assisted grouping."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal

GROUPING_VERSION = "grouping-v1"
PROMPT_VERSION = "grouping-prompt-v1"
AI_KEY_PREFIX = "ai-v1"

_BROAD_SUBCATEGORIES = {
    "",
    "bags_luggage",
    "gloves_scarves",
    "jewelry_watches",
    "misc",
    "miscellaneous",
    "other",
    "socks_underwear",
    "ties_pocketsquares",
}
_PRODUCT_TYPE_ALIASES = {
    "hats": "hat",
    "wallets": "wallet",
    "rings": "ring",
    "necklaces": "necklace",
    "bracelets": "bracelet",
    "earrings": "earring",
}
_COLORS = {
    "beige",
    "black",
    "blue",
    "brown",
    "gold",
    "gray",
    "green",
    "grey",
    "multicolor",
    "orange",
    "pink",
    "purple",
    "red",
    "silver",
    "white",
    "yellow",
}
_CONDITION_DESCRIPTORS = {
    "bnib",
    "bnwt",
    "deadstock",
    "excellent",
    "fair",
    "good",
    "mint",
    "new",
    "nib",
    "nwt",
    "pre-owned",
    "preowned",
    "used",
    "vnds",
}
_STANDALONE_SIZES = {
    "xxs",
    "xs",
    "s",
    "m",
    "l",
    "xl",
    "xxl",
    "xxxl",
    "one-size",
    "os",
    "osfa",
}
_MODEL_PRICES = {
    "gemini-2.5-flash-lite": (Decimal("0.05"), Decimal("0.20")),
    "gemini-2.5-flash": (Decimal("0.15"), Decimal("1.25")),
    "gemini-3.5-flash-lite": (Decimal("0.15"), Decimal("1.25")),
    "gemini-3.5-flash": (Decimal("0.75"), Decimal("4.50")),
}
_MILLION = Decimal(1_000_000)


class BudgetExceededError(ValueError):
    """Raised before a request would exceed its approved USD limit."""


def _normalized_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _slug(value: str) -> str:
    return re.sub(r"[^\w]+", "-", _normalized_text(value), flags=re.UNICODE).strip("-")


def compute_input_hash(
    *,
    brand: str,
    category: str | None,
    subcategory: str | None,
    title: str,
    prompt_version: str = PROMPT_VERSION,
) -> str:
    """Hash only the normalized, privacy-safe fields sent to Gemini."""

    payload = {
        "brand": _normalized_text(brand),
        "category": _normalized_text(category),
        "subcategory": _normalized_text(subcategory),
        "title": _normalized_text(title),
        "prompt_version": prompt_version,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def deterministic_product_type(subcategory: str | None) -> str | None:
    """Return a strict type for a clear Grailed subcategory, otherwise defer to Gemini."""

    normalized = _normalized_text(subcategory).replace("-", "_")
    leaf = normalized.rsplit(".", 1)[-1]
    if normalized in _BROAD_SUBCATEGORIES or leaf in _BROAD_SUBCATEGORIES:
        return None
    return _PRODUCT_TYPE_ALIASES.get(leaf, leaf.replace("_", "-"))


def is_valid_model_span(title: str, model_span: str) -> bool:
    """Accept only a non-empty, word-bounded, case-insensitive span from the title."""

    normalized_title = unicodedata.normalize("NFKC", title).casefold()
    normalized_span = unicodedata.normalize("NFKC", model_span).casefold().strip()
    if not normalized_span or len(normalized_span) > 255:
        return False
    escaped = re.escape(normalized_span).replace(r"\ ", r"\s+")
    return (
        re.search(rf"(?<!\w){escaped}(?!\w)", normalized_title, flags=re.IGNORECASE | re.UNICODE)
        is not None
    )


def normalize_model_span(model_span: str) -> str:
    """Remove common variant/condition descriptors before stable-key generation."""

    tokens = re.findall(r"[^\W_]+(?:[.-][^\W_]+)*", _normalized_text(model_span))
    kept: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "size":
            index += 2
            continue
        if token in {"eu", "uk", "us"} and index + 1 < len(tokens):
            index += 2
            continue
        if token not in _COLORS | _CONDITION_DESCRIPTORS | _STANDALONE_SIZES:
            kept.append(token)
        index += 1
    return " ".join(kept)


def stable_ai_key(
    brand: str, product_type: str, model_span: str, *, brand_id: int | None = None
) -> str:
    """Build an immutable brand/type/model key; product types can never collide."""

    brand_slug = _slug(brand)
    type_slug = _slug(product_type)
    normalized_model = normalize_model_span(model_span)
    if brand_id is not None and brand_id < 1:
        raise ValueError("brand_id must be positive")
    if not brand_slug or not type_slug or not normalized_model:
        raise ValueError("brand, product_type and model_span must be non-empty")
    if brand_id is not None:
        brand_slug = f"{brand_slug}-{brand_id}"
    digest = hashlib.sha256(normalized_model.encode("utf-8")).hexdigest()
    return f"{AI_KEY_PREFIX}:{brand_slug}:{type_slug}:{digest}"


def unique_fallback_key(
    brand: str,
    product_type: str,
    *,
    brand_id: int | None = None,
    physical_item_id: int | None,
    listing_id: int,
) -> str:
    """Keep unresolved listings separate while preserving same-item relists."""

    if brand_id is not None and brand_id < 1:
        raise ValueError("brand_id must be positive")
    if physical_item_id is not None and physical_item_id < 1:
        raise ValueError("physical_item_id must be positive")
    if listing_id < 1:
        raise ValueError("listing_id must be positive")
    identity = (
        f"physical:{physical_item_id}" if physical_item_id is not None else f"listing:{listing_id}"
    )
    brand_slug = _slug(brand)
    type_slug = _slug(product_type)
    if not brand_slug or not type_slug:
        raise ValueError("brand and product_type must be non-empty")
    if brand_id is not None:
        brand_slug = f"{brand_slug}-{brand_id}"
    return f"{AI_KEY_PREFIX}:{brand_slug}:{type_slug}:unique:{identity}"


def batch_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Calculate current Gemini Batch cost without binary floating-point rounding."""

    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be non-negative")
    model_name = model.rsplit("/", 1)[-1].casefold()
    try:
        input_price, output_price = _MODEL_PRICES[model_name]
    except KeyError as error:
        raise ValueError(f"unsupported Gemini model: {model}") from error
    return (Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price) / _MILLION


def ensure_within_budget(spent: Decimal, incremental: Decimal, limit: Decimal) -> None:
    """Reject work before its exact estimated total exceeds the approved limit."""

    if spent < 0 or incremental < 0 or limit < 0:
        raise ValueError("budget values must be non-negative")
    total = spent + incremental
    if total > limit:
        raise BudgetExceededError(f"estimated cost {total} exceeds approved budget {limit}")
