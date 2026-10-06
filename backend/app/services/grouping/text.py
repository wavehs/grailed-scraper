"""Word-level text folding shared by titles, dictionaries and configuration phrases."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

# Decimals first so "2.0" stays one token; "#5" keeps its marker; words keep apostrophes.
_WORD = re.compile(r"\d+\.\d+|#\d+|[^\W_]+(?:['’][^\W_]+)*")
_APOSTROPHES = str.maketrans("", "", "'’")
_SUFFIX_DIGIT = re.compile(r"^([a-z]{3,})(\d)$")

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Word:
    """A folded token and the original word it came from (for readable names)."""

    norm: str
    surface: str


def fold(value: str) -> str:
    """Lowercase, strip accents and apostrophes: "Déprimés" -> "deprimes", "l'Amour" -> "lamour"."""

    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.casefold().translate(_APOSTROPHES)


def singular(token: str) -> str:
    """Collapse plurals deterministically; titles and dictionaries share this rule."""

    if len(token) < 4 or not token.isalpha():
        return token
    if token.endswith("ies") and len(token) >= 5:
        return token[:-3] + "y"
    if token.endswith(("sses", "shes", "ches", "xes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is", "os")):
        return token[:-1]
    return token


def words(text: str) -> list[Word]:
    """Split text into folded, singular tokens; "track.2" and "track2" give "track", "2"."""

    result: list[Word] = []
    for match in _WORD.finditer(text):
        surface = match.group()
        norm = fold(surface)
        if "." in norm:
            whole, _, fraction = norm.partition(".")
            # "2.0" is a version number; other decimals ("17.5") are sizes.
            norm = whole if set(fraction) == {"0"} else norm
            result.append(Word(norm, surface))
            continue
        suffix = _SUFFIX_DIGIT.match(norm)
        if suffix is not None:
            result.append(Word(suffix.group(1), surface[: len(suffix.group(1))]))
            result.append(Word(suffix.group(2), surface[len(suffix.group(1)) :]))
            continue
        result.append(Word(singular(norm), surface))
    return result


def phrase(text: str) -> tuple[str, ...]:
    return tuple(word.norm for word in words(text))


class PhraseIndex(Generic[T]):
    """Longest-match lookup of token phrases with an attached payload."""

    def __init__(self, entries: Iterable[tuple[Sequence[str], T]] = ()) -> None:
        self._by_first: dict[str, list[tuple[tuple[str, ...], T]]] = {}
        for tokens, payload in entries:
            self.add(tokens, payload)

    def add(self, tokens: Sequence[str], payload: T) -> None:
        key = tuple(tokens)
        if not key:
            return
        bucket = self._by_first.setdefault(key[0], [])
        if any(existing == key for existing, _ in bucket):
            return
        bucket.append((key, payload))
        bucket.sort(key=lambda item: -len(item[0]))

    def longest_at(self, tokens: Sequence[str], start: int) -> tuple[int, T] | None:
        for key, payload in self._by_first.get(tokens[start], ()):
            end = start + len(key)
            if end <= len(tokens) and tuple(tokens[start:end]) == key:
                return len(key), payload
        return None

    def __bool__(self) -> bool:
        return bool(self._by_first)
