"""Cached, single-flight Grailed discovery orchestration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.listings import to_utc_datetime
from app.repositories.discovery import DiscoveryRepository
from app.services.sources.grailed.discovery.client import (
    DiscoveryAlgoliaClient,
    DiscoveryHttpError,
)
from app.services.sources.grailed.discovery.credential_discovery import capture_browser_seed
from app.services.sources.grailed.discovery.facet_prober import probe_facets
from app.services.sources.grailed.discovery.index_prober import probe_indices
from app.services.sources.grailed.discovery.js_bundle_fallback import discover_from_bundles
from app.services.sources.grailed.discovery.key_introspection import introspect_key
from app.services.sources.grailed.discovery.models import (
    DiscoveryResult,
    DiscoverySeed,
    DiscoveryStatus,
    KeyCapabilities,
    SchemaChange,
)
from app.services.sources.grailed.discovery.schema_sampler import (
    compare_schemas,
    sample_schema,
)
from app.services.transport.protocols import BrowserSession, HttpTransport

KNOWN_LISTING_INDICES = (
    "Listing_production",
    "Listing_by_date_added_production",
    "Listing_by_low_price_production",
    "Listing_by_high_price_production",
    "Listing_by_popularity_production",
    "Listing_sold_production",
    "Listing_sold_by_date_added_production",
)


class DiscoveryUnavailableError(RuntimeError):
    """No validated public Algolia search credentials could be discovered."""


class DiscoveryService:
    _inflight: ClassVar[dict[tuple[int, str], asyncio.Task[DiscoveryResult]]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        transport: HttpTransport,
        browser: BrowserSession | None = None,
    ) -> None:
        self._repository = DiscoveryRepository(session)
        self._settings = settings
        self._transport = transport
        self._browser = browser

    async def refresh(self, *, force: bool = True) -> DiscoveryResult:
        return await self._singleflight(force=force, invalidate=False)

    async def invalidate_and_refresh(self) -> DiscoveryResult:
        """Mark rejected credentials stale and run one shared forced discovery."""

        return await self._singleflight(force=True, invalidate=True)

    async def _singleflight(self, *, force: bool, invalidate: bool) -> DiscoveryResult:
        loop = asyncio.get_running_loop()
        key = (id(loop), "grailed")
        task = self._inflight.get(key)
        if task is None:
            task = loop.create_task(self._invalidate_then_run(force, invalidate))
            self._inflight[key] = task
            task.add_done_callback(lambda finished: self._clear_inflight(key, finished))
        return await asyncio.shield(task)

    async def _invalidate_then_run(self, force: bool, invalidate: bool) -> DiscoveryResult:
        if invalidate:
            await self._repository.mark_stale()
        return await self._run(force=force)

    async def status(self) -> DiscoveryResult | None:
        credential = await self._repository.credential()
        if credential is None:
            return None
        schema = await self._repository.latest_schema()
        alerts = await self._repository.active_alerts()
        now = datetime.now(UTC)
        expires_at = self._expiry(credential.discovered_at, credential.valid_until)
        status: DiscoveryStatus = "ready"
        if credential.verification_status == "stale" or now >= expires_at:
            status = "stale"
        elif not credential.active_index or not credential.sold_index or not credential.brand_facet:
            status = "degraded"
        return DiscoveryResult(
            source="grailed",
            status=status,
            method=credential.discovery_method,  # type: ignore[arg-type]
            discovered_at=to_utc_datetime(credential.discovered_at) or now,
            expires_at=expires_at,
            app_id=credential.app_id,
            active_index=credential.active_index,
            sold_index=credential.sold_index,
            sorted_indices=tuple(credential.sorted_indices),
            brand_facet=credential.brand_facet,
            category_facet=credential.category_facet,
            key_capabilities=self._capabilities(credential.key_acl, credential.valid_until),
            pagination_limit=credential.pagination_limit,
            max_hits_per_page=credential.max_hits_per_page,
            schema_sample_size=schema.sample_size if schema else 0,
            schema_field_count=len(schema.observed_fields) if schema else 0,
            drift_score=float(schema.drift_score or 0) if schema else 0.0,
            alerts=tuple(
                SchemaChange(
                    severity=alert.severity,  # type: ignore[arg-type]
                    kind=str(alert.details.get("kind")),  # type: ignore[arg-type]
                    path=str(alert.details.get("path", "")),
                    before=tuple(alert.details.get("before", [])),
                    after=tuple(alert.details.get("after", [])),
                )
                for alert in alerts
            ),
        )

    async def mark_stale(self) -> None:
        await self._repository.mark_stale()

    @classmethod
    def is_discovering(cls) -> bool:
        loop_id = id(asyncio.get_running_loop())
        return (loop_id, "grailed") in cls._inflight

    async def _run(self, *, force: bool) -> DiscoveryResult:
        if not force:
            cached = await self.status()
            if cached is not None and cached.status in {"ready", "degraded"}:
                return cached
        previous = await self._repository.latest_schema()
        for auth_attempt in range(2):
            seed = await self._discover_seed()
            client = DiscoveryAlgoliaClient(
                self._transport,
                seed,
                requests_per_minute=self._settings.requests_per_minute,
            )
            try:
                capabilities = await introspect_key(client, seed.api_key)
                if capabilities.max_queries_per_ip_per_hour:
                    hourly_safe_rpm = max(capabilities.max_queries_per_ip_per_hour // 120, 1)
                    client.set_rate_limit(min(self._settings.requests_per_minute, hourly_safe_rpm))
                candidates = self._allowed_indices(seed, capabilities)
                probes = await probe_indices(client, candidates)
                active_index, sold_index = self._index_roles(probes)
                schema_index = sold_index or active_index
                if schema_index is None:
                    raise DiscoveryUnavailableError("No usable listing index was found")
                brand_facet, category_facet, _ = await probe_facets(client, schema_index)
                schema = await sample_schema(
                    client, schema_index, sample_size=self._settings.discovery_sample_size
                )
                break
            except DiscoveryHttpError as exc:
                if exc.status_code not in {401, 403}:
                    raise
                await self._repository.mark_stale()
                if auth_attempt == 1:
                    raise
        previous_fields = previous.observed_fields if previous is not None else None
        drift_score, changes = compare_schemas(previous_fields, schema.fields)
        now = datetime.now(UTC)
        credential, persisted_schema = await self._repository.persist(
            seed=seed,
            capabilities=capabilities,
            probes=probes,
            active_index=active_index,
            sold_index=sold_index,
            brand_facet=brand_facet,
            category_facet=category_facet,
            schema=schema,
            drift_score=drift_score,
            changes=changes,
            now=now,
        )
        del persisted_schema
        return (await self.status()) or self._result_from_fresh(
            seed, capabilities, now, changes, drift_score, credential.valid_until
        )

    async def _discover_seed(self) -> DiscoverySeed:
        if self._browser is not None:
            seed = await capture_browser_seed(
                self._browser, timeout_s=self._settings.discovery_page_timeout_s
            )
            if seed is not None:
                return seed
        for candidate in await discover_from_bundles(self._transport):
            if await self._validate_candidate(candidate):
                return candidate
        raise DiscoveryUnavailableError("Grailed discovery produced no validated candidate")

    async def _validate_candidate(self, seed: DiscoverySeed) -> bool:
        index = seed.indices[0] if seed.indices else KNOWN_LISTING_INDICES[0]
        client = DiscoveryAlgoliaClient(self._transport, seed)
        response = await client.request(
            "POST", client.index_path(index), json_body={"params": "hitsPerPage=1"}
        )
        return response.status_code == 200

    def _expiry(self, discovered_at: datetime, valid_until: datetime | None) -> datetime:
        discovered_utc = to_utc_datetime(discovered_at) or datetime.now(UTC)
        ttl_expiry = discovered_utc + timedelta(hours=self._settings.discovery_ttl_hours)
        valid_utc = to_utc_datetime(valid_until)
        if valid_utc is None:
            return ttl_expiry
        return min(ttl_expiry, valid_utc - timedelta(minutes=10))

    @staticmethod
    def _allowed_indices(seed: DiscoverySeed, capabilities: KeyCapabilities) -> tuple[str, ...]:
        names = [name for name in seed.indices if _is_listing_index(name)]
        names.extend(KNOWN_LISTING_INDICES)
        if capabilities.indexes:
            names = [
                name
                for name in names
                if any(_index_matches(name, pattern) for pattern in capabilities.indexes)
            ]
        return tuple(dict.fromkeys(names))

    @staticmethod
    def _index_roles(probes: tuple[object, ...]) -> tuple[str | None, str | None]:
        names = [getattr(probe, "name", "") for probe in probes]
        sold = next((name for name in names if "sold" in name.casefold()), None)
        active = next(
            (
                name
                for name in names
                if "sold" not in name.casefold() and "listing" in name.casefold()
            ),
            None,
        )
        return active, sold

    @staticmethod
    def _capabilities(payload: dict[str, object], valid_until: datetime | None) -> KeyCapabilities:
        raw_acl = payload.get("acl", [])
        raw_indexes = payload.get("indexes", [])
        return KeyCapabilities(
            acl=tuple(str(item) for item in raw_acl if isinstance(item, str))
            if isinstance(raw_acl, list)
            else (),
            indexes=tuple(str(item) for item in raw_indexes if isinstance(item, str))
            if isinstance(raw_indexes, list)
            else (),
            valid_until=to_utc_datetime(valid_until),
            max_queries_per_ip_per_hour=_optional_int(payload.get("maxQueriesPerIPPerHour")),
            max_hits_per_query=_optional_int(payload.get("maxHitsPerQuery")),
        )

    def _result_from_fresh(
        self,
        seed: DiscoverySeed,
        capabilities: KeyCapabilities,
        now: datetime,
        changes: tuple[SchemaChange, ...],
        drift_score: float,
        valid_until: datetime | None,
    ) -> DiscoveryResult:
        return DiscoveryResult(
            source="grailed",
            status="ready",
            method=seed.method,
            discovered_at=now,
            expires_at=self._expiry(now, valid_until),
            app_id=seed.app_id,
            active_index=None,
            sold_index=None,
            sorted_indices=(),
            brand_facet=None,
            category_facet=None,
            key_capabilities=capabilities,
            pagination_limit=None,
            max_hits_per_page=None,
            schema_sample_size=0,
            schema_field_count=0,
            drift_score=drift_score,
            alerts=changes,
        )

    @classmethod
    def _clear_inflight(cls, key: tuple[int, str], task: asyncio.Task[DiscoveryResult]) -> None:
        if cls._inflight.get(key) is task:
            cls._inflight.pop(key, None)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _index_matches(name: str, pattern: str) -> bool:
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return name == pattern


def _is_listing_index(name: str) -> bool:
    lowered = name.casefold()
    return "listing" in lowered and "suggestion" not in lowered
