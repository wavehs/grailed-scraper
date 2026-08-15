"""Automatic and manual mapping between product brands and source facets."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from rapidfuzz.fuzz import ratio

from app.db.models import Brand
from app.repositories.brands import BrandRepository
from app.services.sources.grailed.algolia.models import AlgoliaQuery, FacetValue


class FacetSearch(Protocol):
    async def search_facet_values(
        self,
        index_name: str,
        facet_name: str,
        facet_query: str,
        *,
        query: AlgoliaQuery | None = None,
    ) -> tuple[FacetValue, ...]: ...


@dataclass(frozen=True, slots=True)
class AutoMapSummary:
    processed: int
    verified: int
    review: int
    unresolved: int


class BrandMappingService:
    def __init__(
        self,
        repository: BrandRepository,
        facets: FacetSearch,
        *,
        active_index: str,
        brand_facet: str = "designers.name",
    ) -> None:
        self._repository = repository
        self._facets = facets
        self._active_index = active_index
        self._brand_facet = brand_facet

    async def auto_map(self, brand_ids: list[int] | None = None) -> AutoMapSummary:
        brands = await self._repository.all(brand_ids)
        verified = review = unresolved = 0
        for brand in brands:
            candidates = await self._candidates(brand)
            if not candidates:
                unresolved += 1
                continue
            max_count = max(candidate.count for candidate in candidates) or 1
            accepted = False
            for candidate in candidates:
                score = self._score(brand, candidate, max_count)
                if score < Decimal("0.75"):
                    continue
                auto_verified = score >= Decimal("0.95")
                mapping = await self._repository.upsert_candidate(
                    brand_id=brand.id,
                    source_name=candidate.value,
                    listings_count=candidate.count,
                    score=score,
                    verified=auto_verified,
                    is_subbrand=_is_subbrand(brand, candidate.value),
                    now=datetime.now(UTC),
                )
                if mapping.rejected_at is not None:
                    continue
                accepted = True
                if auto_verified:
                    verified += 1
                else:
                    review += 1
            if not accepted:
                unresolved += 1
        return AutoMapSummary(len(brands), verified, review, unresolved)

    async def resolve_brand(self, raw_name: str) -> int | None:
        normalized = normalize_brand_name(raw_name)
        brands = await self._repository.all()
        best_brand: Brand | None = None
        best_score: float = 0
        for brand in brands:
            verified_names = [
                item.source_designer_name
                for item in brand.source_mappings
                if item.verified and item.rejected_at is None
            ]
            names = [brand.name, *brand.aliases, *verified_names]
            if any(normalize_brand_name(name) == normalized for name in names):
                return brand.id
            score = max(
                (ratio(normalized, normalize_brand_name(name)) for name in names),
                default=0,
            )
            if score > best_score:
                best_brand, best_score = brand, score
        if best_brand is not None and best_score >= 92:
            return best_brand.id
        await self._repository.record_unmatched(
            raw_name,
            normalized,
            suggested_brand_id=best_brand.id if best_brand else None,
            best_score=Decimal(best_score) / Decimal(100) if best_brand else None,
        )
        return None

    @staticmethod
    def facet_filters(brand: Brand) -> tuple[str | tuple[str, ...], ...]:
        values = [
            mapping.source_designer_name
            for mapping in brand.source_mappings
            if mapping.verified
            and mapping.rejected_at is None
            and (brand.include_subbrands or not mapping.is_subbrand)
        ]
        return ((tuple(f"designers.name:{value}" for value in values)),) if values else ()

    async def _candidates(self, brand: Brand) -> tuple[FacetValue, ...]:
        merged: dict[str, FacetValue] = {}
        for term in dict.fromkeys((brand.name, *brand.aliases)):
            for candidate in await self._facets.search_facet_values(
                self._active_index, self._brand_facet, term
            ):
                previous = merged.get(candidate.value)
                if previous is None or candidate.count > previous.count:
                    merged[candidate.value] = candidate
        return tuple(merged.values())

    @staticmethod
    def _score(brand: Brand, candidate: FacetValue, max_count: int) -> Decimal:
        names = (brand.name, *brand.aliases)
        fuzzy = (
            max(
                ratio(normalize_brand_name(name), normalize_brand_name(candidate.value))
                for name in names
            )
            / 100
        )
        popularity = math.log1p(candidate.count) / math.log1p(max_count)
        return Decimal(str(0.7 * fuzzy + 0.3 * popularity)).quantize(Decimal("0.00001"))


def normalize_brand_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def _is_subbrand(brand: Brand, candidate: str) -> bool:
    normalized = normalize_brand_name(candidate)
    return normalized not in {normalize_brand_name(name) for name in (brand.name, *brand.aliases)}


