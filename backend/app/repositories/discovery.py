"""Persistence operations for discovery cache, schemas, and drift alerts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SchemaAlert, SourceCredential, SourceSchema
from app.services.sources.grailed.discovery.models import (
    DiscoverySeed,
    IndexProbe,
    KeyCapabilities,
    SchemaChange,
    SchemaSample,
)


class DiscoveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def credential(self, source: str = "grailed") -> SourceCredential | None:
        result = await self._session.execute(
            select(SourceCredential).where(SourceCredential.source == source)
        )
        return result.scalar_one_or_none()

    async def latest_schema(self, source: str = "grailed") -> SourceSchema | None:
        result = await self._session.execute(
            select(SourceSchema)
            .where(SourceSchema.source == source)
            .order_by(SourceSchema.detected_at.desc(), SourceSchema.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def active_alerts(self, source: str = "grailed") -> tuple[SchemaAlert, ...]:
        rows = await self._session.scalars(
            select(SchemaAlert)
            .where(SchemaAlert.source == source, SchemaAlert.resolved_at.is_(None))
            .order_by(SchemaAlert.created_at.desc())
        )
        return tuple(rows)

    async def mark_stale(self, source: str = "grailed") -> None:
        credential = await self.credential(source)
        if credential is not None:
            credential.verification_status = "stale"
            await self._session.commit()

    async def persist(
        self,
        *,
        seed: DiscoverySeed,
        capabilities: KeyCapabilities,
        probes: tuple[IndexProbe, ...],
        active_index: str | None,
        sold_index: str | None,
        brand_facet: str | None,
        category_facet: str | None,
        schema: SchemaSample,
        drift_score: float,
        changes: tuple[SchemaChange, ...],
        now: datetime,
    ) -> tuple[SourceCredential, SourceSchema]:
        credential = await self.credential()
        sorted_indices = [probe.name for probe in probes if probe.sort_field]
        pagination_limits = [
            probe.pagination_limit for probe in probes if probe.pagination_limit is not None
        ]
        hit_limits = [probe.max_hits_per_page for probe in probes]
        values = {
            "app_id": seed.app_id,
            "api_key": seed.api_key,
            "algolia_agent": seed.algolia_agent,
            "active_index": active_index,
            "sold_index": sold_index,
            "sorted_indices": sorted_indices,
            "brand_facet": brand_facet,
            "category_facet": category_facet,
            "key_acl": {
                "acl": list(capabilities.acl),
                "indexes": list(capabilities.indexes),
                "can_browse": capabilities.can_browse,
                "maxQueriesPerIPPerHour": capabilities.max_queries_per_ip_per_hour,
                "maxHitsPerQuery": capabilities.max_hits_per_query,
            },
            "pagination_limit": min(pagination_limits) if pagination_limits else None,
            "max_hits_per_page": min(hit_limits) if hit_limits else None,
            "valid_until": capabilities.valid_until,
            "discovered_at": now,
            "discovery_method": seed.method,
            "last_verified_at": now,
            "verification_status": "valid",
        }
        if credential is None:
            credential = SourceCredential(source="grailed", **values)
            self._session.add(credential)
        else:
            for name, value in values.items():
                setattr(credential, name, value)

        source_schema = SourceSchema(
            source="grailed",
            observed_fields=schema.fields,
            sample_size=schema.sample_size,
            pagination_strategy=(
                "browse"
                if capabilities.can_browse
                else "keyset"
                if sorted_indices
                else "range_split"
            ),
            detected_at=now,
            drift_score=drift_score,
        )
        self._session.add(source_schema)
        await self._session.flush()
        await self._sync_alerts(source_schema.id, changes, now)
        await self._session.commit()
        return credential, source_schema

    async def _sync_alerts(
        self, schema_id: int, changes: tuple[SchemaChange, ...], now: datetime
    ) -> None:
        existing = await self.active_alerts()
        existing_keys = {
            (str(alert.details.get("kind")), str(alert.details.get("path"))): alert
            for alert in existing
        }
        for change in changes:
            if (change.kind, change.path) in existing_keys:
                continue
            self._session.add(
                SchemaAlert(
                    source="grailed",
                    source_schema_id=schema_id,
                    severity=change.severity,
                    message=f"Schema {change.kind}: {change.path}",
                    details={
                        "kind": change.kind,
                        "path": change.path,
                        "before": list(change.before),
                        "after": list(change.after),
                    },
                    created_at=now,
                )
            )

