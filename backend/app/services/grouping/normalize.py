"""Title normalization: drop brand, product-type words, colors, sizes and marketing noise."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from rapidfuzz.fuzz import ratio

from app.services.grouping.policy import GroupingPolicy
from app.services.grouping.text import PhraseIndex, Word, phrase, words

_SEASON = re.compile(r"^(?:ss|fw|aw|sp|fa|pf)\d{2,4}$")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_ATTACHED_SIZE = re.compile(
    r"^(?:size|sz|eu|us|uk|it|fr|jp|w|l)\d+(?:\.\d+)?$|^\d+(?:\.\d+)?(?:eu|us|uk|it|fr|jp|cm|mm)$"
)
_BRAND_FUZZY_MIN_CHARS = 6
_BRAND_FUZZY_SCORE = 85
# Seller article codes ("o1bcso1str0226"): long tokens whose letters and digits alternate.
_ARTICLE_MIN_LENGTH = 6
_ARTICLE_MIN_SWITCHES = 2

LexiconKind = Literal["strong", "weak", "color", "noise", "size", "stop"]


@dataclass(frozen=True, slots=True)
class NormalizedTitle:
    """Model tokens (with their original words) and the product types named in the title."""

    tokens: tuple[str, ...]
    surfaces: tuple[str, ...]
    strong_types: tuple[str, ...]
    weak_types: tuple[str, ...]

    @property
    def text(self) -> str:
        return " ".join(self.tokens)


class TitleNormalizer:
    """Brand-aware normalizer; the same instance normalizes titles and dictionary aliases."""

    def __init__(
        self,
        policy: GroupingPolicy,
        *,
        brand_terms: Iterable[str] = (),
        brand_slug: str | None = None,
        stopwords: Iterable[str] = (),
    ) -> None:
        self._policy = policy
        word_lists = policy.words
        self._spelling: PhraseIndex[tuple[str, ...]] = PhraseIndex(word_lists.spelling)
        self._brand: PhraseIndex[bool] = PhraseIndex()
        self._brand_strings: list[tuple[int, str]] = []
        for term in brand_terms:
            tokens = self._spell([Word(token, token) for token in phrase(term)])
            norms = tuple(item.norm for item in tokens)
            if not norms:
                continue
            self._brand.add(norms, True)
            joined = " ".join(norms)
            if len(joined.replace(" ", "")) >= _BRAND_FUZZY_MIN_CHARS:
                self._brand_strings.append((len(norms), joined))
        lexicon: PhraseIndex[tuple[LexiconKind, str]] = PhraseIndex()
        # Insertion order breaks ties between equal phrases: types win over colors and noise.
        for product_type in policy.taxonomy.types.values():
            for words_phrase in product_type.words:
                lexicon.add(words_phrase, ("strong", product_type.id))
        for product_type in policy.taxonomy.types.values():
            for words_phrase in product_type.weak:
                lexicon.add(words_phrase, ("weak", product_type.id))
        for value in stopwords:
            lexicon.add(phrase(value), ("stop", ""))
        for size_phrase in word_lists.size_tokens:
            lexicon.add(size_phrase, ("size", ""))
        for color in (*word_lists.colors, *word_lists.brand_colors.get(brand_slug or "", ())):
            lexicon.add(color, ("color", ""))
        for noise in word_lists.noise:
            lexicon.add(noise, ("noise", ""))
        self._lexicon = lexicon

    def normalize(self, title: str) -> NormalizedTitle:
        tokens = self._spell(words(title))
        tokens = self._remove_brand(tokens)
        tokens = self._remove_sizes(tokens)
        kept: list[Word] = []
        strong: list[str] = []
        weak: list[str] = []
        index = 0
        norms = [item.norm for item in tokens]
        while index < len(tokens):
            hit = self._lexicon.longest_at(norms, index)
            if hit is not None:
                length, (kind, type_id) = hit
                if kind == "strong" and type_id not in strong:
                    strong.append(type_id)
                elif kind == "weak" and type_id not in weak:
                    weak.append(type_id)
                index += length
                continue
            token = tokens[index]
            if not _numeric_noise(token.norm) and not article_code(token.norm):
                kept.append(token)
            index += 1
        return NormalizedTitle(
            tokens=tuple(item.norm for item in kept),
            surfaces=tuple(item.surface for item in kept),
            strong_types=tuple(strong),
            weak_types=tuple(weak),
        )

    def phrase(self, text: str) -> tuple[str, ...]:
        """Normalize a dictionary alias exactly like a title."""

        return self.normalize(text).tokens

    def spelled(self, text: str) -> tuple[str, ...]:
        """Folded and respelled words, before the brand and the lexicon are removed."""

        return tuple(item.norm for item in self._spell(words(text)))

    def _spell(self, tokens: Sequence[Word]) -> list[Word]:
        if not self._policy.words.spelling:
            return list(tokens)
        norms = [item.norm for item in tokens]
        result: list[Word] = []
        index = 0
        while index < len(tokens):
            hit = self._spelling.longest_at(norms, index)
            if hit is None:
                result.append(tokens[index])
                index += 1
                continue
            length, replacement = hit
            surface = " ".join(item.surface for item in tokens[index : index + length])
            result.extend(Word(norm, surface) for norm in replacement)
            index += length
        return result

    def _remove_brand(self, tokens: list[Word]) -> list[Word]:
        if not self._brand:
            return tokens
        norms = [item.norm for item in tokens]
        keep = [True] * len(tokens)
        found = False
        index = 0
        while index < len(tokens):
            hit = self._brand.longest_at(norms, index)
            if hit is None:
                index += 1
                continue
            for offset in range(hit[0]):
                keep[index + offset] = False
            found = True
            index += hit[0]
        if not found:
            # Typos such as "Cheome Hearts" or "Rick Ownes": one fuzzy window per brand term.
            for size, joined in self._brand_strings:
                for start in range(0, len(tokens) - size + 1):
                    window = " ".join(norms[start : start + size])
                    if abs(len(window) - len(joined)) > 2:
                        continue
                    if ratio(window, joined) >= _BRAND_FUZZY_SCORE:
                        for offset in range(size):
                            keep[start + offset] = False
                        break
        return [token for token, flag in zip(tokens, keep, strict=True) if flag]

    def _remove_sizes(self, tokens: list[Word]) -> list[Word]:
        word_lists = self._policy.words
        result: list[Word] = []
        index = 0
        while index < len(tokens):
            norm = tokens[index].norm
            if _ATTACHED_SIZE.match(norm):
                index += 1
                continue
            if norm in word_lists.size_markers:
                following = tokens[index + 1].norm if index + 1 < len(tokens) else None
                index += 1
                if following is not None and (
                    following in word_lists.size_letters
                    or following[:1].isdigit()
                    or any(following == item[0] for item in word_lists.size_tokens)
                ):
                    index += 1
                continue
            result.append(tokens[index])
            index += 1
        return result


def article_code(token: str) -> bool:
    """A seller's article number is no part of a model name: ``o1bcso1str0226``, ``a1b2c3``."""

    if len(token) < _ARTICLE_MIN_LENGTH or not token.isalnum():
        return False
    switches = sum(left.isdigit() != right.isdigit() for left, right in pairwise(token))
    return switches >= _ARTICLE_MIN_SWITCHES


def _numeric_noise(token: str) -> bool:
    """Sizes (10-99, decimals), years and season codes are not part of a model name."""

    if _YEAR.match(token) or _SEASON.match(token):
        return True
    if "." in token:
        return True
    if token.isdigit():
        return 10 <= int(token) <= 99
    return False
