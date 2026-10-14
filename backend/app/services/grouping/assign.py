"""Pure product-type and model assignment for one listing."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from rapidfuzz.fuzz import ratio

from app.services.grouping.normalize import NormalizedTitle
from app.services.grouping.policy import REVIEW_TYPE, Taxonomy
from app.services.grouping.text import PhraseIndex

FUZZY_MIN_LENGTH = 5
FUZZY_SCORE = 90

TypeMethod = Literal["category", "title", "weak", "model", "fallback", "conflict"]


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """One dictionary model of a brand and product type (a line or a version)."""

    key: int
    product_type: str
    name: str
    phrases: tuple[tuple[str, ...], ...]
    parent: int | None = None
    status: str = "confirmed"
    infer: bool = False
    weight: int = 0


@dataclass(frozen=True, slots=True)
class ModelMatch:
    entry: ModelEntry
    tokens: tuple[str, ...]
    fuzzy: bool
    version: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class TypeDecision:
    product_type: str
    method: TypeMethod
    known_category: bool


class ModelDictionary:
    """Longest-phrase lookup of a brand's models, separately for every product type."""

    def __init__(
        self,
        entries: Iterable[ModelEntry],
        *,
        protected_modifiers: frozenset[str] = frozenset(),
        version_numbers: frozenset[str] = frozenset(),
    ) -> None:
        self._protected = protected_modifiers
        self._versions = version_numbers
        self._index: dict[str, PhraseIndex[ModelEntry]] = {}
        self._vocabulary: dict[str, dict[int, set[str]]] = {}
        self._phrases: dict[tuple[str, tuple[str, ...]], ModelEntry] = {}
        self._fuzzy_cache: dict[tuple[str, str], str | None] = {}
        # One-word phrases of confirmed models: "Hour Glass" in a title is the bag Hourglass.
        self._solid: dict[str, set[str]] = {}
        ordered = sorted(entries, key=lambda item: (item.status != "confirmed", item.key))
        for entry in ordered:
            index = self._index.setdefault(entry.product_type, PhraseIndex())
            vocabulary = self._vocabulary.setdefault(entry.product_type, {})
            for tokens in entry.phrases:
                index.add(tokens, entry)
                self._phrases.setdefault((entry.product_type, tokens), entry)
                if len(tokens) == 1 and entry.status == "confirmed":
                    self._solid.setdefault(entry.product_type, set()).add(tokens[0])
                for token in tokens:
                    if len(token) >= FUZZY_MIN_LENGTH and not token.isdigit():
                        vocabulary.setdefault(len(token), set()).add(token)

    def find(self, product_type: str, tokens: Sequence[str]) -> ModelEntry | None:
        return self._phrases.get((product_type, tuple(tokens)))

    def match(self, product_type: str, tokens: Sequence[str]) -> ModelMatch | None:
        index = self._index.get(product_type)
        if not index or not tokens:
            return None
        found = self._search(product_type, index, tokens)
        fuzzy = False
        if found is None:
            corrected = [self._correct(product_type, token) for token in tokens]
            if corrected != list(tokens):
                found = self._search(product_type, index, corrected)
                fuzzy = found is not None
        if found is None:
            return None
        tokens, (start, length, entry), _ = found
        matched = tuple(tokens[start : start + length])
        return ModelMatch(entry, matched, fuzzy, self._version(entry, tokens, start, length))

    def infer_type(self, candidates: Sequence[str], tokens: Sequence[str]) -> str | None:
        """Pick a type for an ambiguous category from the brand's known models."""

        found: list[tuple[bool, int, int, int, str]] = []
        for order, product_type in enumerate(candidates):
            index = self._index.get(product_type)
            hit = self._search(product_type, index, tokens) if index else None
            if hit is not None:
                _, (_, _, entry), covered = hit
                found.append((not entry.infer, -entry.weight, -covered, order, product_type))
        return min(found)[-1] if found else None

    def _search(
        self, product_type: str, index: PhraseIndex[ModelEntry], tokens: Sequence[str]
    ) -> tuple[Sequence[str], tuple[int, int, ModelEntry], int] | None:
        """The best phrase of the title as written, or with two words written as one, and the
        number of title words it covers.

        Two neighbours are glued only into a one-word phrase of a confirmed model; the glued
        match wins only if it covers more words, so "Le Cagole" never loses to a glued word.
        """

        best = _best(index, tokens)
        if product_type not in self._solid or len(tokens) < 2:
            return (tokens, best, best[1]) if best is not None else None
        glued, widths = self._glue(product_type, tokens)
        if len(glued) < len(tokens):
            other = _best(index, glued)
            if other is not None:
                start, length, _ = other
                covered = sum(widths[start : start + length])
                if best is None or covered > best[1]:
                    return glued, other, covered
        return (tokens, best, best[1]) if best is not None else None

    def _glue(
        self, product_type: str, tokens: Sequence[str]
    ) -> tuple[list[str], list[int]]:
        solid = self._solid[product_type]
        glued: list[str] = []
        widths: list[int] = []
        index = 0
        while index < len(tokens):
            pair = "".join(tokens[index : index + 2])
            if index + 1 < len(tokens) and pair in solid:
                glued.append(pair)
                widths.append(2)
                index += 2
                continue
            glued.append(tokens[index])
            widths.append(1)
            index += 1
        return glued, widths

    def _correct(self, product_type: str, token: str) -> str:
        if len(token) < FUZZY_MIN_LENGTH or token.isdigit():
            return token
        cache_key = (product_type, token)
        if cache_key not in self._fuzzy_cache:
            vocabulary = self._vocabulary.get(product_type, {})
            best: tuple[float, str] | None = None
            for size in (len(token) - 1, len(token), len(token) + 1):
                for candidate in vocabulary.get(size, ()):
                    score = ratio(token, candidate, score_cutoff=FUZZY_SCORE)
                    if score and (best is None or (score, candidate) > best):
                        best = (score, candidate)
            self._fuzzy_cache[cache_key] = best[1] if best else None
        return self._fuzzy_cache[cache_key] or token

    def _version(
        self, entry: ModelEntry, tokens: Sequence[str], start: int, length: int
    ) -> tuple[str, ...] | None:
        """"Mega Geobasket", "Track 2", "#5": a version inside the matched line."""

        if entry.parent is not None:
            return None
        modifiers = tuple(
            token for token in tokens[max(0, start - 2) : start] if token in self._protected
        )
        end = start + length
        following = tokens[end] if end < len(tokens) else None
        number = (
            following
            if following is not None
            and (following in self._versions or (following.startswith("#") and len(following) > 1))
            else None
        )
        if not modifiers and number is None:
            return None
        return (*modifiers, *tokens[start:end], *((number,) if number else ()))


def classify_type(
    taxonomy: Taxonomy,
    category_path: str | None,
    title: NormalizedTitle,
    dictionary: ModelDictionary | None = None,
) -> TypeDecision:
    """Category first; title words refine mixed categories; contradictions go to review."""

    rule, known = taxonomy.rule_for(category_path)
    types = taxonomy.types
    strong = [item for item in title.strong_types if item in types]
    if rule.fixed is not None:
        own = types[rule.fixed]
        if not strong or own.id in strong or own.id in title.weak_types:
            return TypeDecision(own.id, "category", known)
        if any(types[item].family == own.family or types[item].section == own.section
               for item in strong):
            return TypeDecision(own.id, "category", known)
        return TypeDecision(REVIEW_TYPE, "conflict", known)
    candidates = rule.any or tuple(strong) or tuple(
        item for item in title.weak_types if item in types
    )
    for product_type in candidates:
        if product_type in strong:
            return TypeDecision(product_type, "title", known)
    fallback = rule.fallback or REVIEW_TYPE
    if strong and rule.any:
        section = types[fallback].section
        if not any(types[item].section == section for item in strong):
            return TypeDecision(REVIEW_TYPE, "conflict", known)
    for product_type in candidates:
        if product_type in title.weak_types:
            return TypeDecision(product_type, "weak", known)
    if dictionary is not None and candidates:
        inferred = dictionary.infer_type(candidates, title.tokens)
        if inferred is not None:
            return TypeDecision(inferred, "model", known)
    return TypeDecision(fallback, "fallback", known)


def _best(
    index: PhraseIndex[ModelEntry], tokens: Sequence[str]
) -> tuple[int, int, ModelEntry] | None:
    best: tuple[int, int, ModelEntry] | None = None
    for start in range(len(tokens)):
        hit = index.longest_at(tokens, start)
        if hit is None:
            continue
        length, entry = hit
        if best is None or _rank(length, entry) > _rank(best[1], best[2]):
            best = (start, length, entry)
    return best


def _rank(length: int, entry: ModelEntry) -> tuple[int, bool, int]:
    return length, entry.status == "confirmed", -entry.key
