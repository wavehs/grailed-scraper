"""Typed values exchanged by the Grailed discovery pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

DiscoveryMethod = Literal["intercept", "bundle", "manual"]
DiscoveryStatus = Literal["ready", "stale", "discovering", "degraded", "unavailable"]


@dataclass(frozen=True, slots=True)
class DiscoverySeed:
    app_id: str
    api_key: str
    algolia_agent: str | None = None
    indices: tuple[str, ...] = ()
    facet_filters: tuple[str, ...] = ()
    session_headers: tuple[tuple[str, str], ...] = ()
    method: DiscoveryMethod = "intercept"


@dataclass(frozen=True, slots=True)
class KeyCapabilities:
    acl: tuple[str, ...] = ()
    indexes: tuple[str, ...] = ()
    valid_until: datetime | None = None
    max_queries_per_ip_per_hour: int | None = None
    max_hits_per_query: int | None = None

    @property
    def can_browse(self) -> bool:
        return "browse" in self.acl


@dataclass(frozen=True, slots=True)
class IndexProbe:
    name: str
    nb_hits: int
    pagination_limit: int | None
    max_hits_per_page: int
    sort_field: str | None = None


@dataclass(frozen=True, slots=True)
class SchemaSample:
    fields: dict[str, dict[str, Any]]
    sample_size: int


@dataclass(frozen=True, slots=True)
class SchemaChange:
    severity: Literal["info", "high"]
    kind: Literal["added", "removed", "type_changed"]
    path: str
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    source: str
    status: DiscoveryStatus
    method: DiscoveryMethod
    discovered_at: datetime
    expires_at: datetime
    app_id: str
    active_index: str | None
    sold_index: str | None
    sorted_indices: tuple[str, ...]
    brand_facet: str | None
    category_facet: str | None
    key_capabilities: KeyCapabilities
    pagination_limit: int | None
    max_hits_per_page: int | None
    schema_sample_size: int
    schema_field_count: int
    drift_score: float = 0.0
    alerts: tuple[SchemaChange, ...] = field(default_factory=tuple)
