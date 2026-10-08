"""Whitelisted collaborations: a line of their own for listings that name no model."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.grouping.text import PhraseIndex, fold, phrase

# Service groups keep a leading underscore so mined phrases can never collide with them.
COLLAB_PREFIX = "_collab-"
_TOKEN = re.compile(r"[^\W_]+")


def partner_key(value: str) -> str:
    """"Yeezy Gap" -> "yeezy-gap": the slug part, also how eval labels name a partner."""

    return "-".join(_TOKEN.findall(fold(value)))


@dataclass(frozen=True, slots=True)
class Collab:
    """One partner of the brand. The title decides placement; ``designers`` only feed reports."""

    name: str
    aliases: tuple[str, ...]
    designers: frozenset[str]  # folded Grailed designer names
    # Product types the partner makes (from ``only_sections``); empty means every type.
    only_types: frozenset[str] = frozenset()
    # Words that cancel a match right after the phrase: "croc embossed" is no Crocs item.
    not_before: tuple[str, ...] = ()

    def applies(self, product_type: str) -> bool:
        return not self.only_types or product_type in self.only_types

    @property
    def slug(self) -> str:
        return COLLAB_PREFIX + partner_key(self.name)

    @property
    def terms(self) -> tuple[str, ...]:
        """The name and every alias: brand terms of the brand, and phrases of the title."""

        return (self.name, *self.aliases)


class CollabIndex:
    """Longest-phrase lookup of collaborations in title tokens taken before brand removal.

    Collaboration names are brand terms, so the normalized title no longer has them; the
    lookup runs on the folded and respelled words (``TitleNormalizer.spelled``).
    """

    def __init__(
        self, collabs: Iterable[Collab], spell: Callable[[str], tuple[str, ...]]
    ) -> None:
        self.collabs = tuple(collabs)
        self._index: PhraseIndex[Collab] = PhraseIndex()
        self._stops: dict[str, frozenset[str]] = {}
        for collab in self.collabs:
            for term in collab.terms:
                self._index.add(spell(term), collab)
            self._stops[collab.slug] = frozenset(
                token for word in collab.not_before for token in spell(word)
            )

    def find(self, tokens: Sequence[str], product_type: str) -> Collab | None:
        """The longest phrase in the title tokens; the leftmost one wins a tie.

        A partner limited to other types, or a phrase followed by one of its ``not_before``
        words, does not count.
        """

        if not self._index or not tokens:
            return None
        best: tuple[int, Collab] | None = None
        for start in range(len(tokens)):
            # A cancelled phrase gives way to a shorter one at the same word.
            hit = next(
                (
                    (length, collab)
                    for length, collab in self._index.matches_at(tokens, start)
                    if self._counts(collab, tokens, start + length, product_type)
                ),
                None,
            )
            if hit is not None and (best is None or hit[0] > best[0]):
                best = hit
        return best[1] if best else None

    def _counts(
        self, collab: Collab, tokens: Sequence[str], after: int, product_type: str
    ) -> bool:
        if not collab.applies(product_type):
            return False
        return after >= len(tokens) or tokens[after] not in self._stops[collab.slug]


def parse_collabs(
    entries: Iterable[Any], source: str, sections: Mapping[str, Iterable[str]] | None = None
) -> tuple[Collab, ...]:
    """Read ``[entry...]``: a name, or ``{name, aliases, designers, only_sections, not_before}``.

    ``designers`` defaults to the name. ``sections`` maps a taxonomy section to its types. A
    phrase names one collaboration, otherwise the line of a title would be ambiguous.
    """

    result: list[Collab] = []
    owners: dict[tuple[str, ...], str] = {}
    slugs: set[str] = set()
    for raw in entries:
        entry = {"name": raw} if isinstance(raw, str) else dict(raw)
        if not entry.get("name"):
            raise ValueError(f"{source}: a collaboration entry has no name")
        name = str(entry["name"]).strip()
        aliases = tuple(
            item
            for item in dict.fromkeys(str(value).strip() for value in entry.get("aliases") or ())
            if item and item != name
        )
        designers = frozenset(
            fold(str(value)).strip() for value in entry.get("designers") or (name,)
        )
        only_types = _section_types(entry.get("only_sections") or (), sections or {}, name, source)
        not_before = tuple(str(value).strip() for value in entry.get("not_before") or ())
        if any(not phrase(value) for value in not_before):
            raise ValueError(f"{source}: collaboration {name!r} has an empty not_before word")
        collab = Collab(name, aliases, designers, only_types, not_before)
        if not partner_key(name):
            raise ValueError(f"{source}: collaboration {name!r} has no words")
        if collab.slug in slugs:
            raise ValueError(f"{source}: collaboration {name!r} is listed twice")
        slugs.add(collab.slug)
        for term in collab.terms:
            tokens = phrase(term)
            if not tokens:
                raise ValueError(f"{source}: collaboration {name!r} alias {term!r} has no words")
            owner = owners.setdefault(tokens, name)
            if owner != name:
                raise ValueError(
                    f"{source}: {' '.join(tokens)!r} is listed for both {owner!r} and {name!r}"
                )
        result.append(collab)
    return tuple(result)


def _section_types(
    names: Iterable[Any], sections: Mapping[str, Iterable[str]], name: str, source: str
) -> frozenset[str]:
    result: set[str] = set()
    for section in (str(value) for value in names):
        if section not in sections:
            raise ValueError(
                f"{source}: collaboration {name!r} refers to unknown section {section!r}"
            )
        result.update(sections[section])
    return frozenset(result)
