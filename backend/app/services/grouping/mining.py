"""Find new model names among listings without a model: frequent phrases across sellers."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TypeVar

MIN_SELLERS = 5
MAX_WORDS = 3
MAX_PHRASES = 1000
# A longer phrase is a name only if most uses of its shorter part come with it
# ("neck logo" vs "neck"), not an incidental pairing ("geobasket mainline").
COLLOCATION_SHARE = 0.5
# A phrase is a fragment if most of its sellers use it only inside longer names ("Speed" of
# "Speed Hunters", "World" of "World Food Programme"): it never becomes a parent line.
FRAGMENT_SHARE = 0.5

T = TypeVar("T")


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
    # Sellers who use the phrase outside the longer known and accepted phrases.
    own: int = 0

    @property
    def fragment(self) -> bool:
        return self.own < FRAGMENT_SHARE * self.sellers


def mine_phrases(
    samples: Sequence[MiningSample],
    *,
    excluded: Iterable[tuple[str, ...]] = (),
    generic: frozenset[str] = frozenset(),
    generic_phrases: Iterable[tuple[str, ...]] = (),
    keep: Iterable[tuple[str, ...]] = (),
    keep_min_sellers: int | None = None,
    min_sellers: int = MIN_SELLERS,
    max_words: int = MAX_WORDS,
    limit: int = MAX_PHRASES,
) -> list[MinedPhrase]:
    """Greedy cover: longer phrases first, a shorter one needs its own seller support.

    ``keep`` are phrases of existing groups: they stay at ``keep_min_sellers`` (hysteresis),
    so a group at the threshold does not appear and disappear from pass to pass.
    """

    blocked = {tuple(item) for item in excluded}
    kept = {tuple(item) for item in keep}
    keep_floor = min_sellers if keep_min_sellers is None else min(keep_min_sellers, min_sellers)
    phrases = frozenset(tuple(item) for item in generic_phrases if item)

    def threshold(gram: tuple[str, ...]) -> int:
        return keep_floor if gram in kept else min_sellers

    occurrences: dict[tuple[str, ...], set[int]] = defaultdict(set)
    surfaces: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    eligible: dict[tuple[str, ...], bool] = {}
    phrase_lengths = sorted({len(item) for item in phrases})
    for position, sample in enumerate(samples):
        tokens = sample.tokens
        # "Mock Neck" is a description: neither "Neck" nor a phrase made of two descriptions
        # ("High Rise" + "Wide Leg" gives "Rise Wide Leg") is counted from this title.
        mask = _forbidden_mask(tokens, generic, phrases, phrase_lengths)
        for size in range(1, max_words + 1):
            for start in range(len(tokens) - size + 1):
                if all(mask[start : start + size]):
                    continue
                gram = tokens[start : start + size]
                allowed = eligible.get(gram)
                if allowed is None:
                    allowed = eligible[gram] = _eligible(gram, generic, phrases)
                if not allowed:
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
        if count >= threshold(gram) and gram not in blocked and _collocation(gram, support)
    ]
    candidates.sort(key=lambda gram: (-len(gram), -support[gram], gram))
    # Known phrases cover their own sub-phrases ("crimes de" inside "crimes de lamour").
    known = [gram for gram in blocked if gram in occurrences]
    accepted: list[tuple[str, ...]] = []
    own: dict[tuple[str, ...], int] = {}
    for gram in candidates:
        covered: set[int] = set()
        for longer in (*known, *accepted):
            if _contains(longer, gram):
                covered |= occurrences[longer]
        residual = sellers(occurrences[gram] - covered)
        if residual >= threshold(gram):
            accepted.append(gram)
            own[gram] = residual
    # The limit cuts the weakest phrases; existing groups go first, so a group at the edge of
    # the limit does not appear and disappear from pass to pass.
    ranked = sorted(accepted, key=lambda gram: (gram not in kept, -support[gram], gram))[:limit]
    return [
        MinedPhrase(
            tokens=gram,
            name=_display(surfaces[gram].most_common(1)[0][0]),
            sellers=support[gram],
            listings=len(occurrences[gram]),
            own=own[gram],
        )
        for gram in ranked
    ]


def parent_line(
    tokens: Sequence[str],
    lines: Iterable[tuple[tuple[str, ...], T]],
    modifiers: frozenset[str] = frozenset(),
) -> T | None:
    """The line a mined phrase is a version of: the longest line that starts the phrase.

    Leading protected modifiers are skipped ("Mini City" is a version of City). A line inside
    the phrase is no parent: "Garde Robe" is not a Robe, "Wide Leg" is not a Leg.
    """

    phrase = tuple(tokens)
    starts = [0]
    while starts[-1] < len(phrase) - 1 and phrase[starts[-1]] in modifiers:
        starts.append(starts[-1] + 1)
    # The longest line wins; at equal length, the one after more modifiers ("Mini City" is a
    # version of City even if "Mini" is a line).
    best: tuple[tuple[int, int], T] | None = None
    for line_tokens, line in lines:
        size = len(line_tokens)
        if size >= len(phrase):
            continue
        for start in reversed(starts):
            rank = (size, start)
            if phrase[start : start + size] == line_tokens and (best is None or rank > best[0]):
                best = (rank, line)
                break
    return best[1] if best else None


def contains(longer: Sequence[str], shorter: Sequence[str]) -> bool:
    return _contains(tuple(longer), tuple(shorter))


def covered(
    gram: Sequence[str],
    words: frozenset[str],
    phrases: frozenset[tuple[str, ...]] = frozenset(),
) -> bool:
    """True if ``gram`` splits entirely into generic words and generic phrases."""

    tokens = tuple(gram)
    reachable = [True] + [False] * len(tokens)
    for end in range(1, len(tokens) + 1):
        for start in range(end):
            if not reachable[start]:
                continue
            piece = tokens[start:end]
            if (len(piece) == 1 and piece[0] in words) or piece in phrases:
                reachable[end] = True
                break
    return bool(tokens) and reachable[-1]


def _forbidden_mask(
    tokens: Sequence[str],
    words: frozenset[str],
    phrases: frozenset[tuple[str, ...]],
    lengths: Sequence[int],
) -> list[bool]:
    """Token positions that belong to a forbidden word or to a forbidden phrase in the title."""

    mask = [token in words for token in tokens]
    for size in lengths:
        for start in range(len(tokens) - size + 1):
            if tuple(tokens[start : start + size]) in phrases:
                mask[start : start + size] = [True] * size
    return mask


def _eligible(
    gram: tuple[str, ...],
    generic: frozenset[str],
    phrases: frozenset[tuple[str, ...]] = frozenset(),
) -> bool:
    if any(token.isdigit() or token.startswith("#") for token in gram):
        return False
    if covered(gram, generic, phrases):
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
