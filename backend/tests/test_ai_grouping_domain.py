"""Pure contracts for safe, deterministic AI grouping."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.config import Settings
from app.services.ai_grouping.domain import (
    GROUPING_VERSION,
    PROMPT_VERSION,
    BudgetExceededError,
    batch_cost_usd,
    compute_input_hash,
    deterministic_product_type,
    ensure_within_budget,
    is_valid_model_span,
    normalize_model_span,
    stable_ai_key,
    unique_fallback_key,
)


def test_clear_subcategories_form_strict_product_type_boundaries() -> None:
    assert deterministic_product_type("accessories.hats") == "hat"
    assert deterministic_product_type("womens_jewelry.rings") == "ring"
    assert deterministic_product_type("accessories.wallets") == "wallet"
    assert deterministic_product_type("accessories.jewelry_watches") is None
    assert deterministic_product_type("accessories.misc") is None
    assert deterministic_product_type("accessories.bags_luggage") is None
    assert deterministic_product_type("accessories.gloves_scarves") is None
    assert deterministic_product_type("accessories.socks_underwear") is None
    assert deterministic_product_type("accessories.ties_pocketsquares") is None
    assert deterministic_product_type("womens_bags_luggage.other") is None


def test_model_span_must_be_an_exact_bounded_unicode_substring() -> None:
    title = "Chrome Hearts Cröss Hat"
    assert is_valid_model_span(title, "CRÖSS HAT")
    assert is_valid_model_span("Straße Hat", "STRASSE")
    assert not is_valid_model_span(title, "Cross Ring")
    assert not is_valid_model_span("Chrome Hearts Crossbody Bag", "Cross")
    assert not is_valid_model_span(title, "")


def test_color_size_and_condition_descriptors_do_not_split_a_model() -> None:
    assert normalize_model_span("Cross Hat Black Size L New") == "cross hat"
    assert normalize_model_span("Cross Hat white size M used") == "cross hat"
    assert stable_ai_key("Chrome Hearts", "hat", "Cross Hat Black Size L New") == stable_ai_key(
        "chrome hearts", "hat", "Cross Hat white size M used"
    )


def test_stable_keys_never_cross_product_types_and_fallback_prefers_physical_item() -> None:
    hat = stable_ai_key("Chrome Hearts", "hat", "Cross")
    ring = stable_ai_key("Chrome Hearts", "ring", "Cross")

    assert hat.startswith("ai-v1:chrome-hearts:hat:")
    assert hat != ring
    assert unique_fallback_key("Chrome Hearts", "hat", physical_item_id=7, listing_id=99).endswith(
        ":physical:7"
    )
    assert unique_fallback_key(
        "Chrome Hearts", "hat", physical_item_id=None, listing_id=99
    ).endswith(":listing:99")


def test_stable_keys_include_immutable_brand_identity_when_available() -> None:
    first = stable_ai_key("Foo Bar", "hat", "Cross", brand_id=1)
    second = stable_ai_key("Foo-Bar", "hat", "Cross", brand_id=2)

    assert first != second
    assert unique_fallback_key(
        "Foo Bar", "hat", brand_id=1, physical_item_id=None, listing_id=9
    ) != unique_fallback_key("Foo-Bar", "hat", brand_id=2, physical_item_id=None, listing_id=9)


def test_input_hash_is_normalized_and_prompt_versioned() -> None:
    first = compute_input_hash(
        brand="Chrome Hearts",
        category="Accessories",
        subcategory="accessories.hats",
        title="  Cross   Hat ",
    )
    second = compute_input_hash(
        brand="chrome hearts",
        category="accessories",
        subcategory="ACCESSORIES.HATS",
        title="cross hat",
    )

    assert GROUPING_VERSION == "grouping-v1"
    assert PROMPT_VERSION == "grouping-prompt-v1"
    assert first == second
    assert first != compute_input_hash(
        brand="Chrome Hearts",
        category="Accessories",
        subcategory="accessories.hats",
        title="Cross Hat",
        prompt_version="grouping-prompt-v2",
    )


def test_batch_prices_and_budget_guard_use_exact_decimals() -> None:
    assert batch_cost_usd("gemini-2.5-flash-lite", 1_000_000, 1_000_000) == Decimal("0.25")
    assert batch_cost_usd("gemini-2.5-flash", 1_000_000, 1_000_000) == Decimal("1.40")
    assert batch_cost_usd("gemini-2.5-flash-lite", 1, 1) == Decimal("0.00000025")
    assert batch_cost_usd("gemini-3.5-flash-lite", 1_000_000, 1_000_000) == Decimal("1.40")
    assert batch_cost_usd("gemini-3.5-flash", 1_000_000, 1_000_000) == Decimal("5.25")

    ensure_within_budget(Decimal("4.90"), Decimal("0.10"), Decimal("5.00"))
    with pytest.raises(BudgetExceededError, match="5.01.*5.00"):
        ensure_within_budget(Decimal("4.90"), Decimal("0.11"), Decimal("5.00"))


def test_gemini_key_is_secret_and_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_GEMINI_API_KEY", "local-secret")
    settings = Settings()

    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "local-secret"
    assert "local-secret" not in repr(settings)
