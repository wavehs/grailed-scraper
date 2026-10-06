"""Find new model names among listings without a model: frequent phrases across sellers."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

MIN_SELLERS = 5
MAX_WORDS = 3
MAX_PHRASES = 1000
# A longer phrase is a name only if most uses of its shorter part come with it
# ("neck logo" vs "neck"), not an incidental pairing ("geobasket mainline").
COLLOCATION_SHARE = 0.5


@dataclass(frozen=True, slots=True)
class MiningSample:
    """Normalized model tokens of one listing; ``seller`` defends against one spamming seller."""

    seller: str
    tokens: tuple[str, ...]
    surfaces: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MinedPhrase:
    tokens: tuple[str, ...]
    name: str
    sellers: int
    listings: int


def mine_phrases(
    samples: Sequence[MiningSample],
    *,
    excluded: Iterable[tuple[str, ...]] = (),
    generic: frozenset[str] = frozenset(),
    min_sellers: int = MIN_SELLERS,
    max_words: int = MAX_WORDS,
    limit: int = MAX_PHRASES,
) -> list[MinedPhrase]:
    """Greedy cover: longer phrases first, a shorter one needs its own seller support."""

    blocked = {tuple(item) for item in excluded}
    occurrences: dict[tuple[str, ...], set[int]] = defaultdict(set)
    surfaces: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    for position, sample in enumerate(samples):
        tokens = sample.tokens
        for size in range(1, max_words + 1):
            for start in range(len(tokens) - size + 1):
                gram = tokens[start : start + size]
                if not _eligible(gram, generic):
                    continue
                if position not in occurrences[gram]:
                    occurrences[gram].add(position)
                    surfaces[gram][" ".join(sample.surfaces[start : start + size])] += 1

    def sellers(listing_ids: Iterable[int]) -> int:
        return len({samples[item].seller for item in listing_ids})

    support = {gram: sellers(found) for gram, found in occurrences.items()}
    candidates = [
        gram
        for gram, count in support.items()
        if count >= min_sellers and gram not in blocked and _collocation(gram, support)
    ]
    candidates.sort(key=lambda gram: (-len(gram), -support[gram], gram))
    # Known phrases cover their own sub-phrases ("crimes de" inside "crimes de lamour").
    known = [gram for gram in blocked if gram in occurrences]
    accepted: list[tuple[str, ...]] = []
    for gram in candidates:
        covered: set[int] = set()
        for longer in (*known, *accepted):
            if _contains(longer, gram):
                covered |= occurrences[longer]
        residual = occurrences[gram] - covered
        if sellers(residual) >= min_sellers:
            accepted.append(gram)
    ranked = sorted(accepted, key=lambda gram: (-support[gram], gram))[:limit]
    return [
        MinedPhrase(
            tokens=gram,
            name=_display(surfaces[gram].most_common(1)[0][0]),
            sellers=support[gram],
            listings=len(occurrences[gram]),
        )
        for gram in ranked
    ]


def contains(longer: Sequence[str], shorter: Sequence[str]) -> bool:
    return _contains(tuple(longer), tuple(shorter))


def _eligible(gram: tuple[str, ...], generic: frozenset[str]) -> bool:
    if any(token.isdigit() or token.startswith("#") for token in gram):
        return False
    if all(token in generic for token in gram):
        return False
    if len(gram) == 1:
        return len(gram[0]) >= 3
    return len(gram[0]) >= 2 and len(gram[-1]) >= 2


def _collocation(gram: tuple[str, ...], support: dict[tuple[str, ...], int]) -> bool:
    if len(gram) < 2:
        return True
    return all(
        support[gram] >= COLLOCATION_SHARE * support[part]
        for part in (gram[:-1], gram[1:])
        if part in support
    )


def _contains(longer: tuple[str, ...], shorter: tuple[str, ...]) -> bool:
    size = len(shorter)
    return size < len(longer) and any(
        longer[start : start + size] == shorter for start in range(len(longer) - size + 1)
    )


def _display(surface: str) -> str:
    return " ".join(
        word if any(char.isupper() for char in word) else word.title() for word in surface.split()
    )
