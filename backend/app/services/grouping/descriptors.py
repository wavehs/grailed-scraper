"""Descriptor dictionary: phrases that describe a listing (fit, material...) but are not models."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.grouping.text import PhraseIndex, phrase

# Service groups keep a leading underscore so mined phrases can never collide with them.
DESCRIPTOR_PREFIX = "_desc-"
# docs/GROUPING.md §0: the classes of a descriptor, also used by the labels (DESCRIPTOR:<class>).
DESCRIPTOR_CLASSES = frozenset(
    {"fit", "size", "material", "print", "detail", "optics", "subtype", "style", "brandmark"}
)
BRAND_CLASS = "brandmark"


@dataclass(frozen=True, slots=True)
class Descriptor:
    """One descriptive phrase: ``phrases[0]`` is the name, the rest are spellings of it."""

    name: str
    klass: str
    phrases: tuple[tuple[str, ...], ...]
    only_types: frozenset[str] = frozenset()
    except_types: frozenset[str] = frozenset()

    @property
    def slug(self) -> str:
        return DESCRIPTOR_PREFIX + "-".join(self.phrases[0])

    @property
    def aliases(self) -> list[str]:
        return [" ".join(tokens) for tokens in self.phrases[1:]]

    def applies(self, product_type: str) -> bool:
        if self.only_types and product_type not in self.only_types:
            return False
        return product_type not in self.except_types


@dataclass(frozen=True, slots=True)
class DescriptorMatch:
    descriptor: Descriptor
    start: int
    length: int


@dataclass(slots=True, init=False)
class DescriptorIndex:
    """Longest-phrase lookup of the descriptors that apply to a product type."""

    descriptors: tuple[Descriptor, ...]
    _indexes: dict[str, PhraseIndex[Descriptor]] = field(repr=False)
    _phrases: dict[str, frozenset[tuple[str, ...]]] = field(repr=False)

    def __init__(self, descriptors: Iterable[Descriptor] = ()) -> None:
        self.descriptors = tuple(descriptors)
        self._indexes = {}
        self._phrases = {}

    def phrases(self, product_type: str) -> frozenset[tuple[str, ...]]:
        """Every phrase (names and spellings) that is no model in ``product_type``."""

        if product_type not in self._phrases:
            self._phrases[product_type] = frozenset(
                tokens
                for descriptor in self.descriptors
                if descriptor.applies(product_type)
                for tokens in descriptor.phrases
            )
        return self._phrases[product_type]

    def find(self, product_type: str, tokens: Sequence[str]) -> DescriptorMatch | None:
        """The longest phrase in the title tokens; the leftmost one wins a tie."""

        index = self._index(product_type)
        if not index or not tokens:
            return None
        best: DescriptorMatch | None = None
        for start in range(len(tokens)):
            hit = index.longest_at(tokens, start)
            if hit is not None and (best is None or hit[0] > best.length):
                best = DescriptorMatch(hit[1], start, hit[0])
        return best

    def _index(self, product_type: str) -> PhraseIndex[Descriptor]:
        index = self._indexes.get(product_type)
        if index is None:
            index = PhraseIndex()
            for descriptor in self.descriptors:
                if descriptor.applies(product_type):
                    for tokens in descriptor.phrases:
                        index.add(tokens, descriptor)
            self._indexes[product_type] = index
        return index


def parse_descriptors(
    payload: Mapping[str, Any] | Iterable[Any],
    types: Iterable[str],
    source: str,
    *,
    klass: str | None = None,
) -> tuple[Descriptor, ...]:
    """Read ``{class: [entry...]}``, or a flat list of entries when ``klass`` is given.

    An entry is a phrase, or ``{name, aliases, only_types, except_types}``.
    """

    known = frozenset(types)
    if klass is not None:
        groups: list[tuple[str, Iterable[Any]]] = [(klass, payload)]
    else:
        assert isinstance(payload, Mapping)
        groups = [(str(name), entries or ()) for name, entries in payload.items()]
    result: list[Descriptor] = []
    for class_name, entries in groups:
        if class_name not in DESCRIPTOR_CLASSES:
            raise ValueError(f"{source}: unknown descriptor class {class_name}")
        for raw in entries:
            result.append(_descriptor(raw, class_name, known, source))
    check_unique(result, known, source)
    return tuple(result)


def _descriptor(raw: Any, klass: str, known: frozenset[str], source: str) -> Descriptor:
    entry = {"name": raw} if isinstance(raw, str) else dict(raw)
    name = _display(str(entry["name"]))
    names = [name, *(str(item) for item in entry.get("aliases") or ())]
    phrases = tuple(dict.fromkeys(tokens for value in names if (tokens := phrase(value))))
    if not phrases:
        raise ValueError(f"{source}: descriptor {name!r} has no words")
    only = frozenset(str(item) for item in entry.get("only_types") or ())
    excepted = frozenset(str(item) for item in entry.get("except_types") or ())
    unknown = sorted((only | excepted) - known)
    if unknown:
        raise ValueError(f"{source}: descriptor {name!r} refers to unknown types {unknown}")
    return Descriptor(name, klass, phrases, only, excepted)


def _display(name: str) -> str:
    """"wide leg" -> "Wide Leg"; words with a capital ("Y2K", "BB") are kept."""

    return " ".join(
        word if any(char.isupper() for char in word) else word.title() for word in name.split()
    )


def check_unique(
    descriptors: Sequence[Descriptor], known: Iterable[str], source: str
) -> None:
    """A phrase names one descriptor per product type, otherwise its group would be ambiguous."""

    known = frozenset(known)
    owners: dict[tuple[str, tuple[str, ...]], int] = {}
    for index, descriptor in enumerate(descriptors):
        types = (descriptor.only_types or known) - descriptor.except_types
        for tokens in descriptor.phrases:
            for product_type in types:
                key = (product_type, tokens)
                if owners.setdefault(key, index) != index:
                    raise ValueError(
                        f"{source}: {' '.join(tokens)!r} is listed for both "
                        f"{descriptors[owners[key]].name!r} and {descriptor.name!r} "
                        f"in {product_type}"
                    )
