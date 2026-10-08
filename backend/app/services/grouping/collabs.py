"""Whitelisted collaborations: a line of their own for listings that name no model."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
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
        for collab in self.collabs:
            for term in collab.terms:
                self._index.add(spell(term), collab)

    def find(self, tokens: Sequence[str]) -> Collab | None:
        """The longest phrase in the title tokens; the leftmost one wins a tie."""

        if not self._index or not tokens:
            return None
        best: tuple[int, Collab] | None = None
        for start in range(len(tokens)):
            hit = self._index.longest_at(tokens, start)
            if hit is not None and (best is None or hit[0] > best[0]):
                best = hit
        return best[1] if best else None


def parse_collabs(entries: Iterable[Any], source: str) -> tuple[Collab, ...]:
    """Read ``[entry...]``: a name, or ``{name, aliases, designers}``.

    ``designers`` defaults to the name. A phrase names one collaboration, otherwise the line
    of a title would be ambiguous.
    """

    result: list[Collab] = []
    owners: dict[tuple[str, ...], str] = {}
    slugs: set[str] = set()
    for raw in entries:
        entry = {"name": raw} if isinstance(raw, str) else dict(raw)
        name = str(entry["name"]).strip()
        aliases = tuple(
            item
            for item in dict.fromkeys(str(value).strip() for value in entry.get("aliases") or ())
            if item and item != name
        )
        designers = frozenset(
            fold(str(value)).strip() for value in entry.get("designers") or (name,)
        )
        collab = Collab(name, aliases, designers)
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
