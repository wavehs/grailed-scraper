"""Load the grouping configuration once: taxonomy, word lists and seed model dictionaries."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from app.core.config import PROJECT_ROOT
from app.services.grouping.descriptors import (
    BRAND_CLASS,
    Descriptor,
    DescriptorIndex,
    check_unique,
    parse_descriptors,
)
from app.services.grouping.text import phrase

CONFIG_DIRECTORY = PROJECT_ROOT / "config"
REVIEW_TYPE = "review"
UNKNOWN_DEPARTMENT = "_unknown"


@dataclass(frozen=True, slots=True)
class ProductType:
    id: str
    section: str
    family: str
    ru: str
    en: str
    words: tuple[tuple[str, ...], ...] = ()
    weak: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class CategoryRule:
    """A clear category has ``fixed``; a mixed one picks from ``any`` by title words."""

    fixed: str | None
    any: tuple[str, ...] = ()
    fallback: str | None = None


@dataclass(frozen=True, slots=True)
class Taxonomy:
    version: str
    sections: dict[str, dict[str, str]]
    types: dict[str, ProductType]
    categories: dict[str, CategoryRule]
    departments: dict[str, CategoryRule]

    def rule_for(self, category_path: str | None) -> tuple[CategoryRule, bool]:
        """Return the rule and whether the exact path was known."""

        path = (category_path or "").strip().casefold()
        if path in self.categories:
            return self.categories[path], True
        department = path.partition(".")[0]
        return self.departments.get(department, self.departments[UNKNOWN_DEPARTMENT]), False


@dataclass(frozen=True, slots=True)
class WordLists:
    spelling: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]
    noise: tuple[tuple[str, ...], ...]
    generic: frozenset[str]
    colors: tuple[tuple[str, ...], ...]
    brand_colors: dict[str, tuple[tuple[str, ...], ...]]
    size_markers: frozenset[str]
    size_tokens: tuple[tuple[str, ...], ...]
    size_letters: frozenset[str]
    protected_modifiers: frozenset[str]
    version_numbers: frozenset[str]


@dataclass(frozen=True, slots=True)
class MiningPolicy:
    """Stability of mined groups: an existing group is kept at a lower threshold."""

    keep_ratio: float = 0.6
    retire_ttl_days: int = 90
    # Sellers with at least this many listings in the brand do not vote for new models.
    wholesale_listings: int | None = None


@dataclass(frozen=True, slots=True)
class SeedModel:
    name: str
    aliases: tuple[str, ...]
    types: tuple[str, ...] | None  # None: any product type of the brand
    parent: str | None = None
    infer: str | None = None


@dataclass(frozen=True, slots=True)
class GroupingPolicy:
    taxonomy: Taxonomy
    words: WordLists
    seeds: dict[str, tuple[SeedModel, ...]] = field(default_factory=dict)
    digest: str = ""
    mining: MiningPolicy = field(default_factory=MiningPolicy)
    # Phrases that describe a listing but are no model: shared by all brands, or one brand's.
    descriptors: tuple[Descriptor, ...] = ()
    brand_descriptors: dict[str, tuple[Descriptor, ...]] = field(default_factory=dict)

    def seed_models(self, brand_slug: str | None) -> tuple[SeedModel, ...]:
        return self.seeds.get(brand_slug or "", ())

    def descriptor_index(self, brand_slug: str | None) -> DescriptorIndex:
        return DescriptorIndex(
            (*self.descriptors, *self.brand_descriptors.get(brand_slug or "", ()))
        )


@lru_cache(maxsize=4)
def load_policy(directory: Path = CONFIG_DIRECTORY) -> GroupingPolicy:
    """Parse and validate the YAML files once per process (call ``cache_clear`` in tests)."""

    taxonomy_path = directory / "taxonomy.yaml"
    words_path = directory / "grouping.yaml"
    seed_paths = sorted((directory / "models").glob("*.yaml"))
    digest = hashlib.sha256()
    for path in (taxonomy_path, words_path, *seed_paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    taxonomy = _taxonomy(_yaml(taxonomy_path))
    words_payload = _yaml(words_path)
    words = _words(words_payload)
    mining = _mining(words_payload.get("mining") or {})
    descriptors = parse_descriptors(
        words_payload.get("descriptors") or {}, taxonomy.types, words_path.name
    )
    seeds: dict[str, tuple[SeedModel, ...]] = {}
    brand_descriptors: dict[str, tuple[Descriptor, ...]] = {}
    for path in seed_paths:
        payload = _yaml(path)
        seeds[path.stem] = _seed_models(payload, taxonomy, path.name)
        # A brand's own marks (paris, maison, demna...) are descriptors of the brandmark class.
        marks = parse_descriptors(
            payload.get("generic") or (), taxonomy.types, path.name, klass=BRAND_CLASS
        )
        if marks:
            check_unique((*descriptors, *marks), taxonomy.types, path.name)
            brand_descriptors[path.stem] = marks
    return GroupingPolicy(
        taxonomy, words, seeds, digest.hexdigest(), mining, descriptors, brand_descriptors
    )


def _yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a YAML object")
    return payload


def _phrases(values: Any) -> tuple[tuple[str, ...], ...]:
    result = [phrase(str(value)) for value in values or ()]
    return tuple(dict.fromkeys(item for item in result if item))


def _taxonomy(payload: dict[str, Any]) -> Taxonomy:
    sections = {str(key): dict(value) for key, value in payload["sections"].items()}
    types: dict[str, ProductType] = {}
    for type_id, raw in payload["types"].items():
        if raw["section"] not in sections:
            raise ValueError(f"Type {type_id} has an unknown section")
        types[str(type_id)] = ProductType(
            id=str(type_id),
            section=str(raw["section"]),
            family=str(raw.get("family", raw["section"])),
            ru=str(raw["ru"]),
            en=str(raw["en"]),
            words=_phrases(raw.get("words")),
            weak=_phrases(raw.get("weak")),
        )
    if REVIEW_TYPE not in types:
        raise ValueError("The taxonomy must define the review type")

    def rule(value: Any, where: str) -> CategoryRule:
        if isinstance(value, str):
            candidates: tuple[str, ...] = (value,)
            parsed = CategoryRule(fixed=value, any=candidates, fallback=value)
        else:
            candidates = tuple(str(item) for item in value.get("any", ()))
            fallback = str(value["fallback"])
            parsed = CategoryRule(fixed=None, any=candidates, fallback=fallback)
            candidates = (*candidates, fallback)
        unknown = [item for item in candidates if item not in types]
        if unknown:
            raise ValueError(f"{where} refers to unknown types {unknown}")
        return parsed

    categories = {
        str(path).casefold(): rule(value, str(path))
        for path, value in payload["categories"].items()
    }
    departments = {
        str(name).casefold(): rule(value, str(name))
        for name, value in payload["departments"].items()
    }
    if UNKNOWN_DEPARTMENT not in departments:
        raise ValueError("The taxonomy must define the _unknown department")
    return Taxonomy(str(payload["version"]), sections, types, categories, departments)


def _words(payload: dict[str, Any]) -> WordLists:
    spelling = tuple(
        (phrase(str(source)), phrase(str(target)))
        for source, target in (payload.get("spelling") or {}).items()
    )
    return WordLists(
        spelling=tuple((source, target) for source, target in spelling if source),
        noise=_phrases(payload.get("noise")),
        generic=frozenset(
            token for value in payload.get("generic", ()) for token in phrase(str(value))
        ),
        colors=_phrases(payload.get("colors")),
        brand_colors={
            str(slug): _phrases(values)
            for slug, values in (payload.get("brand_colors") or {}).items()
        },
        size_markers=frozenset(
            token for value in payload.get("size_markers", ()) for token in phrase(str(value))
        ),
        size_tokens=_phrases(payload.get("size_tokens")),
        size_letters=frozenset(
            token for value in payload.get("size_letters", ()) for token in phrase(str(value))
        ),
        protected_modifiers=frozenset(
            token
            for value in payload.get("protected_modifiers", ())
            for token in phrase(str(value))
        ),
        version_numbers=frozenset(str(value) for value in payload.get("version_numbers", ())),
    )


def _mining(payload: dict[str, Any]) -> MiningPolicy:
    defaults = MiningPolicy()
    keep_ratio = float(payload.get("keep_ratio", defaults.keep_ratio))
    ttl = int(payload.get("retire_ttl_days", defaults.retire_ttl_days))
    if not 0 < keep_ratio <= 1 or ttl < 0:
        raise ValueError("mining.keep_ratio must be in (0, 1] and retire_ttl_days >= 0")
    wholesale = payload.get("wholesale_listings", defaults.wholesale_listings)
    if wholesale is not None and int(wholesale) < 1:
        raise ValueError("mining.wholesale_listings must be empty or >= 1")
    return MiningPolicy(
        keep_ratio=keep_ratio,
        retire_ttl_days=ttl,
        wholesale_listings=int(wholesale) if wholesale is not None else None,
    )


def _seed_models(
    payload: dict[str, Any], taxonomy: Taxonomy, source: str
) -> tuple[SeedModel, ...]:
    models: list[SeedModel] = []
    for raw in payload.get("models") or ():
        name = str(raw["name"]).strip()
        raw_types = raw.get("types", "any")
        types = None if raw_types == "any" else tuple(str(item) for item in raw_types)
        unknown = [item for item in types or () if item not in taxonomy.types]
        infer = raw.get("infer")
        if infer is not None and infer not in taxonomy.types:
            unknown.append(str(infer))
        if unknown:
            raise ValueError(f"{source}: model {name} refers to unknown types {unknown}")
        aliases = tuple(dict.fromkeys([name, *(str(item) for item in raw.get("aliases", ()))]))
        models.append(
            SeedModel(
                name=name,
                aliases=aliases,
                types=types,
                parent=str(raw["parent"]) if raw.get("parent") else None,
                infer=str(infer) if infer else None,
            )
        )
    return tuple(models)
