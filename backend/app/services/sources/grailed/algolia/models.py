"""Request and response models for the supported Algolia read APIs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AlgoliaCredentialsData:
    app_id: str
    api_key: str
    algolia_agent: str | None = None
    session_headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AlgoliaQuery:
    query: str = ""
    hits_per_page: int = 200
    page: int = 0
    filters: str | None = None
    facet_filters: tuple[str | tuple[str, ...], ...] = ()
    numeric_filters: tuple[str, ...] = ()
    attributes_to_retrieve: tuple[str, ...] = ("*",)
    facets: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.hits_per_page < 0:
            raise ValueError("hits_per_page cannot be negative")
        if self.page < 0:
            raise ValueError("page cannot be negative")


@dataclass(frozen=True, slots=True)
class AlgoliaRequest:
    index_name: str
    query: AlgoliaQuery

    def __post_init__(self) -> None:
        if not self.index_name:
            raise ValueError("index_name is required")


@dataclass(frozen=True, slots=True)
class AlgoliaPage:
    hits: tuple[dict[str, Any], ...]
    nb_hits: int
    page: int = 0
    nb_pages: int = 0
    hits_per_page: int = 0
    exhaustive_nb_hits: bool = True
    cursor: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> AlgoliaPage:
        raw_hits = payload.get("hits", [])
        hits = (
            tuple(item for item in raw_hits if isinstance(item, dict))
            if isinstance(raw_hits, list)
            else ()
        )
        return cls(
            hits=hits,
            nb_hits=_integer(payload.get("nbHits"), len(hits)),
            page=_integer(payload.get("page"), 0),
            nb_pages=_integer(payload.get("nbPages"), 0),
            hits_per_page=_integer(payload.get("hitsPerPage"), len(hits)),
            exhaustive_nb_hits=payload.get("exhaustiveNbHits") is not False,
            cursor=payload.get("cursor") if isinstance(payload.get("cursor"), str) else None,
        )


@dataclass(frozen=True, slots=True)
class FacetValue:
    value: str
    count: int
    highlighted: str | None = None


def _integer(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default
