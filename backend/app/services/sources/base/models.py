"""Typed values shared by source fetching implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from app.domain.listings import FetchTier

CoverageStatus = Literal["complete", "partial", "poor", "skipped"]


@dataclass(frozen=True, slots=True)
class RawHit:
    """An unnormalized source record with enough provenance for stage 7."""

    payload: dict[str, Any]
    fetch_tier: FetchTier

    @property
    def object_id(self) -> str | None:
        value = self.payload.get("objectID", self.payload.get("id"))
        if value is None or isinstance(value, bool):
            return None
        return str(value)


@dataclass(frozen=True, slots=True)
class FetchBatch:
    """One resumable page returned by any fetching tier."""

    hits: tuple[RawHit, ...]
    fetch_tier: FetchTier
    cursor: str | None = None
    expected_hits: int | None = None
    truncated: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Completeness result calculated only from unique source identifiers."""

    expected_hits: int
    collected_hits: int
    coverage: Decimal | None
    status: CoverageStatus
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    @classmethod
    def calculate(
        cls,
        *,
        expected_hits: int,
        collected_hits: int,
        truncated: bool = False,
        warnings: tuple[str, ...] = (),
    ) -> CoverageReport:
        if expected_hits < 0 or collected_hits < 0:
            raise ValueError("Coverage counts cannot be negative")
        if expected_hits == 0:
            return cls(0, collected_hits, None, "skipped", truncated, warnings)
        ratio = min(Decimal(collected_hits) / Decimal(expected_hits), Decimal(1))
        if ratio >= Decimal("0.98") and not truncated:
            status: CoverageStatus = "complete"
        elif ratio >= Decimal("0.70"):
            status = "partial"
        else:
            status = "poor"
        return cls(expected_hits, collected_hits, ratio, status, truncated, warnings)
