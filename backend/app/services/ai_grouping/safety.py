"""Conservative product boundaries shared by rules and local inference."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

import yaml  # type: ignore[import-untyped]

from app.core.config import RESOURCE_ROOT

LOCAL_GROUPING_VERSION = "local-grouping-v1"
LOCAL_KEY_PREFIX = "local-v1"


def policy() -> dict[str, Any]:
    with (RESOURCE_ROOT / "config" / "grouping.yaml").open(encoding="utf-8") as source:
        value: dict[str, Any] = yaml.safe_load(source)
    return value


def policy_digest() -> str:
    return hashlib.sha256((RESOURCE_ROOT / "config" / "grouping.yaml").read_bytes()).hexdigest()


def words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def product_type(
    title: str, brand: str, category: str | None, subcategory: str | None
) -> str | None:
    """Abstain on conflicting title/source types, including mixed-item titles."""
    rules = policy()
    tokens = set(words(title)) - set(words(brand))
    inferred = {name for name, spec in rules["types"].items() if tokens & set(spec["words"])}
    for spec in rules["brand_models"].get(brand.casefold(), {}).values():
        if tokens & set(spec["aliases"]):
            inferred.add(spec["type"])
    source_leaf = (subcategory or "").casefold().replace("-", "_").rsplit(".", 1)[-1]
    source_types = {
        name
        for name, spec in rules["types"].items()
        if source_leaf in spec["sources"] or source_leaf == name
    }
    choices = inferred | source_types
    if len(choices) != 1:
        return None
    chosen = next(iter(choices))
    root = (category or "").casefold().split(".")[0]
    allowed = rules["types"][chosen]["categories"]
    if root and root not in allowed:
        return None
    return str(chosen)


def canonical_model(span: str, brand: str) -> str:
    """Only remove explicit color variants; preserve model-defining qualifiers."""
    rules = policy()
    excluded = (
        set(rules["colors"])
        | set(rules["brand_colors"].get(brand.casefold(), []))
        | {word for spec in rules["types"].values() for word in spec["words"]}
    )
    tokens = [word for word in words(span) if word not in excluded]
    aliases = rules["brand_models"].get(brand.casefold(), {})
    for model, spec in aliases.items():
        tokens = [model if token in spec["aliases"] else token for token in tokens]
    return " ".join(tokens)


def model_evidence_valid(title: str, span: str, brand: str, kind: str) -> bool:
    """Require literal evidence and retain explicit qualifiers and model numbers."""
    from app.services.ai_grouping.domain import is_valid_model_span

    if not is_valid_model_span(title, span):
        return False
    normalized = canonical_model(span, brand)
    if not normalized:
        return False
    rules = policy()
    title_tokens = set(words(title))
    model_tokens = set(words(normalized))
    protected = set(rules["protected_modifiers"])
    # Sizes following an explicit marker are not model numbers.
    without_sizes = re.sub(r"\b(?:size|sz|us|uk|eu)\s*\d+(?:\.\d+)?", "", title, flags=re.I)
    model_numbers = {
        token
        for token in words(without_sizes)
        if token.isdigit() and not re.fullmatch(r"(?:19|20)\d{2}", token)
    }
    if ((title_tokens & protected) | model_numbers) - model_tokens:
        return False
    generic = (
        set(words(brand))
        | {word for spec in rules["types"].values() for word in spec["words"]}
        | {"logo", "graphic", "authentic", "rare", "vintage", "new", "used"}
    )
    if not model_tokens - generic:
        return False
    for spec in rules["brand_models"].get(brand.casefold(), {}).values():
        if title_tokens & set(spec["aliases"]) and kind != spec["type"]:
            return False
    return True


def local_input_version() -> str:
    return f"local-prompt-v1:{policy_digest()}"
