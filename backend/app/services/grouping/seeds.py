"""Where a seed model of "any" product type is needed (docs/GROUPING.md §3).

A seed with ``types: any`` gets a group in a product type only once a title of that type
names it: "World Food Programme" needs a T-shirt group, not a ring one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from rapidfuzz.fuzz import ratio

from app.services.grouping.assign import FUZZY_MIN_LENGTH, FUZZY_SCORE, ModelDictionary, ModelEntry
from app.services.grouping.normalize import TitleNormalizer
from app.services.grouping.policy import SeedModel

# Every title is matched against the same small dictionary, whatever its product type.
_ANY_TYPE = "*"
# A glued one-word name ("garderobe") is spelled apart from a part this long ("garde robe").
_GLUE_PART = 2


def seed_types(
    seeds: Sequence[SeedModel],
    titles: Iterable[tuple[str, Sequence[str]]],
    normalizer: TitleNormalizer,
) -> dict[str, set[str]]:
    """Seed name -> product types whose titles ``(type, tokens)`` match an "any" seed.

    Matching is the assignment's own (longest phrase, a glued spelling, a typo), only with
    the "any" seeds in the dictionary; titles without a word close to a seed are skipped.
    """

    entries = [
        ModelEntry(key=index, product_type=_ANY_TYPE, name=seed.name, phrases=phrases)
        for index, seed in enumerate(seeds)
        if seed.types is None
        and (phrases := tuple(dict.fromkeys(
            tokens for value in seed.aliases if (tokens := normalizer.phrase(value))
        )))
    ]
    found: dict[str, set[str]] = {}
    if not entries:
        return found
    dictionary = ModelDictionary(entries)
    clue = _Clue(entries)
    for product_type, title in titles:
        if not any(clue.near(token) for token in title):
            continue
        match = dictionary.match(_ANY_TYPE, title)
        if match is not None:
            found.setdefault(seeds[match.entry.key].name, set()).add(product_type)
    return found


class _Clue:
    """Does a title word bring a title close enough to a seed to be matched at all?"""

    def __init__(self, entries: Sequence[ModelEntry]) -> None:
        self._words = {token for entry in entries for tokens in entry.phrases for token in tokens}
        self._parts = {
            word[:size]
            for entry in entries
            for tokens in entry.phrases
            if len(tokens) == 1
            for word in tokens
            for size in range(_GLUE_PART, len(word))
        }
        self._fuzzy: Mapping[int, list[str]] = _by_length(self._words)
        self._seen: dict[str, bool] = {}

    def near(self, token: str) -> bool:
        known = self._seen.get(token)
        if known is None:
            known = token in self._words or token in self._parts or self._typo(token)
            self._seen[token] = known
        return known

    def _typo(self, token: str) -> bool:
        if len(token) < FUZZY_MIN_LENGTH or token.isdigit():
            return False
        return any(
            ratio(token, word, score_cutoff=FUZZY_SCORE)
            for size in (len(token) - 1, len(token), len(token) + 1)
            for word in self._fuzzy.get(size, ())
        )


def _by_length(words: Iterable[str]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for word in sorted(words):
        if len(word) >= FUZZY_MIN_LENGTH and not word.isdigit():
            grouped.setdefault(len(word), []).append(word)
    return grouped
