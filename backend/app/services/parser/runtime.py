"""In-process parser supervisor with durable checkpoints and cooperative cancellation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import ParserRun, ParserRunTask, SourceCredential
from app.domain.listings import ListingStatus
from app.repositories.fetching import FetchReportRepository
from app.repositories.lifecycle import LifecycleRepository
from app.repositories.listings import ListingRepository
from app.repositories.runs import RunRepository
from app.services.grouping import GroupingService
from app.services.normalization.mapping import load_source_mapping
from app.services.normalization.normalizer import ListingNormalizer, NormalizationContext
from app.services.normalization.quality import QualityProcessor
from app.services.parser.fetching import FetchApi
from app.services.parser.incremental import IncrementalPlanner, RefreshActiveService
from app.services.parser.observability import RunMetrics
from app.services.scoring import OpportunityScoringService, ScoringService
from app.services.sources.base.models import CoverageReport
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.models import AlgoliaCredentialsData, AlgoliaQuery
from app.services.sources.grailed.algolia.pagination import PaginationPlanner, PaginationSpec
from app.services.sources.grailed.discovery.service import DiscoveryService
from app.services.transport.factory import create_http_transport
from app.services.transport.protocols import HttpTransport


@dataclass(slots=True)
class _Resources:
    fetcher: FetchApi
    transport: HttpTransport
    metrics: RunMetrics
    algolia: AlgoliaClient

    async def close(self) -> None:
        await self.transport.close()


class ParserRuntime:
    """Own background tasks; all recoverable state remains in SQLite."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        scoring: ScoringService | None = None,
        market_lock: asyncio.Lock | None = None,
        grouping_lock: asyncio.Lock | None = None,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._scoring = scoring or OpportunityScoringService(sessions)
        self._market_lock = market_lock or asyncio.Lock()
        # Shared with the grouping API so a manual regroup never races the run's regroup.
        self.grouping_lock = grouping_lock or asyncio.Lock()
        self._jobs: dict[int, asyncio.Task[None]] = {}
        self._cancel: dict[int, asyncio.Event] = {}
        self._resources_by_run: dict[int, _Resources] = {}
        self._write_lock = asyncio.Lock()
        self._last_health: dict[str, Any] = {
            "circuits": [],
            "proxies": [],
            "metrics": {},
            "tier": None,
        }
        self._closed = False

    async def reconcile(self) -> int:
        async with self._sessions() as session:
            count = await RunRepository(session).reconcile_interrupted()
            await session.commit()
            return count

    def start(self, run_id: int, *, settings: Settings | None = None) -> None:
        if self._closed:
            raise RuntimeError("Parser runtime is closed")
        existing = self._jobs.get(run_id)
        if existing is not None and not existing.done():
            raise ValueError("run_already_active")
        event = asyncio.Event()
        self._cancel[run_id] = event
        run_settings = settings or self._settings
        task = asyncio.create_task(
            self._execute(run_id, event, run_settings), name=f"parser-run-{run_id}"
        )
        self._jobs[run_id] = task
        task.add_done_callback(lambda finished: self._forget(run_id, finished))

    def cancel(self, run_id: int) -> bool:
        event = self._cancel.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def active_run_ids(self) -> list[int]:
        return sorted(run_id for run_id, task in self._jobs.items() if not task.done())

    def health_snapshot(self) -> dict[str, Any]:
        if not self._resources_by_run:
            return dict(self._last_health)
        resources = next(reversed(self._resources_by_run.values()))
        return {
            "circuits": resources.algolia.circuit_statuses(),
            "metrics": resources.metrics.snapshot(),
            "tier": "T1",
        }

    async def close(self) -> None:
        self._closed = True
        for event in self._cancel.values():
            event.set()
        pending = [task for task in self._jobs.values() if not task.done()]
        if pending:
            done, remaining = await asyncio.wait(pending, timeout=20)
            del done
            for task in remaining:
                task.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)

    async def _execute(self, run_id: int, cancelled: asyncio.Event, settings: Settings) -> None:
        # ponytail: one market mutation lock matches SQLite's single-writer ceiling.
        async with self._market_lock:
            await self._execute_locked(run_id, cancelled, settings)

    async def _execute_locked(
        self, run_id: int, cancelled: asyncio.Event, settings: Settings
    ) -> None:
        heartbeat: asyncio.Task[None] | None = None
        resources: _Resources | None = None
        logger = structlog.get_logger(__name__)
        try:
            logger.info("parser_run_started", run_id=run_id, source="grailed")
            async with self._sessions() as session:
                await RunRepository(session).begin(run_id)
                await session.commit()
            resources = await self._resources(run_id, settings)
            self._resources_by_run[run_id] = resources
            async with self._sessions() as session:
                run = await session.get(ParserRun, run_id)
                assert run is not None
                run.tier_used = "T1"
                await RunRepository(session).heartbeat(run_id)
                await session.commit()
            heartbeat = asyncio.create_task(self._heartbeat(run_id, cancelled, settings))
            task_ids = await self._pending_task_ids(run_id)
            queue: asyncio.Queue[int] = asyncio.Queue()
            for task_id in task_ids:
                queue.put_nowait(task_id)
            workers = [
                asyncio.create_task(
                    self._worker(run_id, queue, resources.fetcher, cancelled, settings)
                )
                for _ in range(min(settings.parser_max_concurrency, max(len(task_ids), 1)))
            ]
            await queue.join()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            async with self._sessions() as session:
                repository = RunRepository(session)
                if cancelled.is_set():
                    await repository.finish(run_id, "cancelled")
                else:
                    await repository.set_phase(run_id, "grouping")
                    await repository.heartbeat(run_id)
                    await session.commit()
                    brand_ids = await repository.brand_ids(run_id)
                    async with self.grouping_lock:
                        grouping_result = await GroupingService(session).regroup(brand_ids)
                    await session.commit()
                    await repository.set_phase(run_id, "scoring")
                    # Release SQLite's write lock before the scoring service opens
                    # its own short-lived transaction for immutable snapshots.
                    await session.commit()
                    scoring_result = await self._scoring.score_run(run_id)
                    run = await repository.get(run_id)
                    assert run is not None
                    metric_snapshot = resources.metrics.snapshot()
                    run.stats = {
                        **run.stats,
                        "grouping": grouping_result.summary(),
                        "scoring": scoring_result,
                        "observability": metric_snapshot,
                        "persistence": {
                            "inserted": metric_snapshot["listings_inserted"],
                            "updated": metric_snapshot["listings_updated"],
                        },
                    }
                    run.requests_made = sum(resources.metrics.requests_by_tier.values())
                    await repository.finish(run_id, await repository.aggregate_status(run_id))
                await session.commit()
            logger.info("parser_run_completed", run_id=run_id, source="grailed")
        except asyncio.CancelledError:
            await self._mark_interrupted(run_id)
            raise
        except Exception as exc:
            structlog.get_logger(__name__).exception(
                "parser_run_failed", run_id=run_id, error_type=type(exc).__name__
            )
            await self._mark_failed(run_id, type(exc).__name__)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if resources is not None:
                try:
                    self._last_health = self.health_snapshot()
                    await self._persist_metrics(run_id, resources.metrics)
                finally:
                    self._resources_by_run.pop(run_id, None)
                    await resources.close()

    async def _worker(
        self,
        run_id: int,
        queue: asyncio.Queue[int],
        fetcher: FetchApi,
        cancelled: asyncio.Event,
        settings: Settings,
    ) -> None:
        while True:
            task_id = await queue.get()
            try:
                with structlog.contextvars.bound_contextvars(
                    run_id=run_id,
                    task_id=task_id,
                    source="grailed",
                ):
                    if cancelled.is_set():
                        continue
                    structlog.get_logger(__name__).info("parser_task_started")
                    await self._process_task(run_id, task_id, fetcher, cancelled, settings)
                    structlog.get_logger(__name__).info("parser_task_completed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                structlog.get_logger(__name__).exception(
                    "parser_task_failed",
                    run_id=run_id,
                    task_id=task_id,
                    source="grailed",
                    error_type=type(exc).__name__,
                )
                await self._fail_task(task_id, type(exc).__name__)
            finally:
                queue.task_done()

    async def _process_task(
        self,
        run_id: int,
        task_id: int,
        fetcher: FetchApi,
        cancelled: asyncio.Event,
        settings: Settings,
    ) -> None:
        async with self._sessions() as session:
            task = await session.get(ParserRunTask, task_id)
            run = await session.get(ParserRun, run_id)
            if task is None or task.status != "pending":
                return
            assert run is not None
            task.status = "running"
            task.attempts += 1
            task.started_at = datetime.now(UTC)
            await session.commit()
            spec_data = dict(task.bucket_spec or {})
            prior_hits = task.hits_collected
            prior_cursor = task.cursor
            mode = run.mode
        if mode == "refresh_active":
            await self._process_refresh(run_id, task_id, spec_data, fetcher, cancelled, settings)
            return
        pagination = PaginationPlanner(fetcher).fetch(
            PaginationSpec(
                index_name=str(spec_data["index_name"]),
                query=_query(dict(spec_data["query"])),
                strategy=settings.algolia_pagination_strategy,
                can_browse=bool(spec_data.get("can_browse")),
                sorted_index=cast(str | None, spec_data.get("sorted_index")),
                key_attrs=tuple(str(value) for value in spec_data.get("key_attrs", ())),
                secondary_attrs=("id", "objectID", "price_i"),
                pagination_limit=int(spec_data.get("pagination_limit", 1_000)),
                hits_per_page=int(
                    dict(spec_data["query"]).get("hits_per_page")
                    or settings.algolia_hits_per_page
                ),
                fetch_tier="T1",
                resume_cursor=prior_cursor,
            )
        )
        last_key: int | None = None
        async for batch in pagination:
            if cancelled.is_set():
                await self._reset_pending(task_id)
                return
            normalized = []
            invalid = 0
            observed = datetime.now(UTC)
            normalizer = ListingNormalizer(load_source_mapping(), settings=settings)
            for hit in batch.hits:
                result = await normalizer.normalize(
                    hit,
                    NormalizationContext(
                        status=cast(ListingStatus, spec_data["index_type"]),
                        parser_run_id=run_id,
                        observed_at=observed,
                        brand_id=cast(int, spec_data["brand_id"]),
                        fetch_tier=batch.fetch_tier,
                    ),
                )
                if result.listing is not None:
                    normalized.append(result.listing)
                else:
                    invalid += 1
                value = hit.payload.get(str(spec_data.get("key_attrs", [""])[0]))
                if isinstance(value, int) and not isinstance(value, bool):
                    last_key = value if last_key is None else max(last_key, value)
            normalized = QualityProcessor(settings).apply(normalized)
            metrics = self._resources_by_run[run_id].metrics
            metrics.record_listings(fetched=len(batch.hits), invalid=invalid)
            for item in normalized:
                metrics.record_quality_flags(item.quality_flags)
            # ponytail: SQLite has one writer; move to a server DB before removing this lock.
            async with self._write_lock:
                async with self._sessions() as session:
                    task = await session.get(ParserRunTask, task_id)
                    run = await session.get(ParserRun, run_id)
                    assert task is not None and run is not None
                    if normalized:
                        upsert = await ListingRepository(session).upsert_batch(normalized)
                        metrics.record_listings(inserted=upsert.inserted, updated=upsert.updated)
                    task.hits_collected += len(batch.hits)
                    task.cursor = batch.cursor or task.cursor
                    run.requests_made = sum(metrics.requests_by_tier.values())
                    await RunRepository(session).heartbeat(run_id)
                    await session.commit()
        report = pagination.report
        async with self._sessions() as session:
            task = await session.get(ParserRunTask, task_id)
            assert task is not None
            collected = min(prior_hits + report.collected_hits, report.expected_hits)
            combined = CoverageReport.calculate(
                expected_hits=report.expected_hits,
                collected_hits=collected,
                truncated=report.truncated,
                warnings=report.warnings,
            )
            await FetchReportRepository(session).finish_task(
                task_id,
                combined,
                fetch_tier="T1",
                cursor=task.cursor,
            )
            metrics = self._resources_by_run[run_id].metrics
            if task.brand_id is not None:
                metrics.coverage_by_brand[str(task.brand_id)] = str(combined.coverage)
            if last_key is not None:
                await IncrementalPlanner(LifecycleRepository(session), settings).complete(
                    brand_id=cast(int, task.brand_id),
                    index_type=task.index_type,
                    last_key_value=str(last_key),
                    mode=str(spec_data.get("mode", "delta")),
                    coverage_complete=combined.status == "complete",
                    truncated=combined.truncated,
                )
            await session.commit()
        if spec_data.get("sweep_missing") and combined.status == "complete":
            await self._sweep_missing(run_id, spec_data, fetcher, cancelled, settings)

    async def _sweep_missing(
        self,
        run_id: int,
        spec_data: dict[str, Any],
        fetcher: FetchApi,
        cancelled: asyncio.Event,
        settings: Settings,
    ) -> None:
        """Check active listings the complete active pass did not see: sold or removed."""

        async with self._write_lock, self._sessions() as session:
            run = await session.get(ParserRun, run_id)
            credential = await session.scalar(
                select(SourceCredential).where(SourceCredential.source == "grailed")
            )
            if run is None or credential is None or credential.sold_index is None:
                return
            result = await RefreshActiveService(
                fetcher,
                LifecycleRepository(session),
                ListingRepository(session),
                ListingNormalizer(load_source_mapping(), settings=settings),
                settings,
                active_index=str(spec_data["index_name"]),
                sold_index=credential.sold_index,
                checkpoint=session.commit,
                should_stop=cancelled.is_set,
            ).run(
                parser_run_id=run_id,
                brand_id=cast(int, spec_data["brand_id"]),
                not_seen_since=run.created_at,
            )
            sweeps = dict(run.stats.get("missing_sweeps", {}))
            sweeps[str(spec_data["brand_id"])] = {
                "checked": result.checked,
                "sold": result.sold,
                "pending": result.pending,
                "removed": result.removed,
            }
            run.stats = {**run.stats, "missing_sweeps": sweeps}
            await session.commit()

    async def _process_refresh(
        self,
        run_id: int,
        task_id: int,
        spec_data: dict[str, Any],
        fetcher: FetchApi,
        cancelled: asyncio.Event,
        settings: Settings,
    ) -> None:
        async with self._sessions() as session:
            credential = await session.scalar(
                select(SourceCredential).where(SourceCredential.source == "grailed")
            )
            if credential is None or credential.sold_index is None:
                raise RuntimeError("discovery_incomplete")
            sold_index = credential.sold_index
            service = RefreshActiveService(
                fetcher,
                LifecycleRepository(session),
                ListingRepository(session),
                ListingNormalizer(load_source_mapping(), settings=settings),
                settings,
                active_index=str(spec_data["index_name"]),
                sold_index=sold_index,
                checkpoint=session.commit,
                should_stop=cancelled.is_set,
            )
            result = await service.run(
                parser_run_id=run_id, brand_id=cast(int, spec_data["brand_id"])
            )
            if cancelled.is_set():
                task = await session.get(ParserRunTask, task_id)
                assert task is not None
                task.status = "pending"
                await session.commit()
                return
            report = CoverageReport.calculate(
                expected_hits=result.checked,
                collected_hits=result.checked,
            )
            await FetchReportRepository(session).finish_task(
                task_id,
                report,
                fetch_tier="T1",
                cursor=None,
            )
            run = await session.get(ParserRun, run_id)
            assert run is not None
            self._resources_by_run[run_id].metrics.record_listings(
                inserted=result.inserted, updated=result.updated
            )
            run.stats = {
                **run.stats,
                "refresh_active": {
                    "checked": result.checked,
                    "active": result.active,
                    "sold": result.sold,
                    "pending": result.pending,
                    "removed": result.removed,
                    "inserted": result.inserted,
                    "updated": result.updated,
                },
            }
            await session.commit()

    async def _resources(self, run_id: int, settings: Settings) -> _Resources:
        async with self._sessions() as session:
            credential = await session.scalar(
                select(SourceCredential).where(SourceCredential.source == "grailed")
            )
            run = await session.get(ParserRun, run_id)
            snapshot = dict(run.stats.get("observability", {})) if run is not None else {}
        metrics = RunMetrics.resume(
            snapshot,
            minimum_requests=run.requests_made if run is not None else 0,
            tier=run.tier_used or "T1" if run is not None else "T1",
        )
        if credential is None:
            raise RuntimeError("discovery_required")
        transport = create_http_transport(settings)
        seed = AlgoliaCredentialsData(
            credential.app_id, credential.api_key, credential.algolia_agent
        )

        async def refresh_credentials() -> AlgoliaCredentialsData:
            async with self._sessions() as session:
                service = DiscoveryService(session, settings, transport)
                await service.invalidate_and_refresh()
                refreshed = await session.scalar(
                    select(SourceCredential).where(SourceCredential.source == "grailed")
                )
                if refreshed is None:
                    raise RuntimeError("discovery_required")
                return AlgoliaCredentialsData(
                    refreshed.app_id, refreshed.api_key, refreshed.algolia_agent
                )

        t1 = AlgoliaClient(
            transport,
            seed,
            requests_per_minute=settings.requests_per_minute,
            max_concurrency=settings.max_concurrent_requests,
            max_retries=settings.parser_max_retries,
            max_requests=settings.parser_max_requests_per_run,
            multiquery_batch_size=settings.algolia_multiquery_batch_size,
            timeout_s=settings.parser_request_timeout_s,
            metrics=metrics,
            tier="T1",
            refresh_credentials=refresh_credentials,
        )
        return _Resources(t1, transport, metrics, t1)

    async def _heartbeat(self, run_id: int, cancelled: asyncio.Event, settings: Settings) -> None:
        while not cancelled.is_set():
            await asyncio.sleep(settings.parser_progress_interval_s)
            async with self._sessions() as session:
                await RunRepository(session).heartbeat(run_id)
                resources = self._resources_by_run.get(run_id)
                if resources is not None:
                    run = await session.get(ParserRun, run_id)
                    assert run is not None
                    run.tier_used = "T1"
                    run.stats = {
                        **run.stats,
                        "observability": resources.metrics.snapshot(),
                    }
                    run.requests_made = sum(resources.metrics.requests_by_tier.values())
                await session.commit()

    async def _persist_metrics(self, run_id: int, metrics: RunMetrics) -> None:
        async with self._sessions() as session:
            run = await session.get(ParserRun, run_id)
            if run is None:
                return
            run.stats = {**run.stats, "observability": metrics.snapshot()}
            run.requests_made = sum(metrics.requests_by_tier.values())
            await session.commit()

    async def _pending_task_ids(self, run_id: int) -> list[int]:
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(ParserRunTask.id).where(
                        ParserRunTask.run_id == run_id, ParserRunTask.status == "pending"
                    )
                )
            )

    async def _reset_pending(self, task_id: int) -> None:
        async with self._sessions() as session:
            task = await session.get(ParserRunTask, task_id)
            if task is not None:
                task.status = "pending"
                await session.commit()

    async def _fail_task(self, task_id: int, error: str) -> None:
        async with self._sessions() as session:
            task = await session.get(ParserRunTask, task_id)
            if task is not None:
                task.status = "failed"
                task.error = error
                task.finished_at = datetime.now(UTC)
                await session.commit()

    async def _mark_failed(self, run_id: int, error: str) -> None:
        async with self._sessions() as session:
            run = await RunRepository(session).get(run_id)
            if run is not None:
                run.warnings = [*run.warnings, f"run failed: {error}"]
                await RunRepository(session).finish(run_id, "failed")
                await session.commit()

    async def _mark_interrupted(self, run_id: int) -> None:
        async with self._sessions() as session:
            run = await RunRepository(session).get(run_id)
            if run is not None and run.status == "running":
                await RunRepository(session).finish(run_id, "interrupted")
                await session.commit()

    def _forget(self, run_id: int, _: asyncio.Task[None]) -> None:
        self._jobs.pop(run_id, None)
        self._cancel.pop(run_id, None)


def _query(payload: dict[str, Any]) -> AlgoliaQuery:
    facet_filters: list[str | tuple[str, ...]] = []
    for item in payload.get("facet_filters", []):
        facet_filters.append(
            tuple(str(value) for value in item) if isinstance(item, list) else str(item)
        )
    return AlgoliaQuery(
        query=str(payload.get("query", "")),
        hits_per_page=int(payload.get("hits_per_page", 200)),
        page=int(payload.get("page", 0)),
        filters=cast(str | None, payload.get("filters")),
        facet_filters=tuple(facet_filters),
        numeric_filters=tuple(str(value) for value in payload.get("numeric_filters", [])),
        attributes_to_retrieve=tuple(
            str(value) for value in payload.get("attributes_to_retrieve", ["*"])
        ),
        facets=tuple(str(value) for value in payload.get("facets", [])),
        extra=dict(payload.get("extra", {})),
    )
