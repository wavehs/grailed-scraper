"""Read-client protocol shared by the Algolia client and pagination planner."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar

from app.services.sources.grailed.algolia.models import (
    AlgoliaPage,
    AlgoliaQuery,
    AlgoliaRequest,
    FacetValue,
)

T = TypeVar("T")


class FetchApi(Protocol):
    async def search(self, index_name: str, query: AlgoliaQuery) -> AlgoliaPage: ...

    async def multi_query(self, requests: Sequence[AlgoliaRequest]) -> tuple[AlgoliaPage, ...]: ...

    async def browse(
        self, index_name: str, query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage: ...

    async def search_facet_values(
        self,
        index_name: str,
        facet_name: str,
        facet_query: str,
        *,
        query: AlgoliaQuery | None = None,
    ) -> tuple[FacetValue, ...]: ...
