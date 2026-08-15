"""Complete Algolia pagination with explicit coverage accounting."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from math import ceil
from typing import Literal, Protocol

from app.domain.listings import FetchTier
from app.services.sources.base.models import CoverageReport, FetchBatch, RawHit
from app.services.sources.grailed.algolia.models import AlgoliaPage, AlgoliaQuery, AlgoliaRequest

PaginationStrategy = Literal["auto", "browse", "keyset", "range_split"]


class AlgoliaReadClient(Protocol):
    async def search(self, index_name: str, query: AlgoliaQuery) -> AlgoliaPage: ...

    async def multi_query(self, requests: list[AlgoliaRequest]) -> tuple[AlgoliaPage, ...]: ...

    async def browse(
        self, index_name: str, query: AlgoliaQuery, *, cursor: str | None = None
    ) -> AlgoliaPage: ...


@dataclass(frozen=True, slots=True)
class PaginationSpec:
    index_name: str
    query: AlgoliaQuery
    strategy: PaginationStrategy = "auto"
    can_browse: bool = False
    sorted_index: str | None = None
    key_attrs: tuple[str, ...] = ("sold_at_i", "created_at_i")
    secondary_attrs: tuple[str, ...] = ("id", "objectID", "price_i")
    pagination_limit: int = 1_000
    hits_per_page: int = 200
    fetch_tier: FetchTier = "T1"
    resume_cursor: str | None = None
    max_hits: int | None = None

    def __post_init__(self) -> None:
        if self.pagination_limit < 1 or self.hits_per_page < 1:
            raise ValueError("Pagination limits must be positive")
        if self.max_hits is not None and self.max_hits < 1:
            raise ValueError("max_hits must be positive")


class PaginationRun:
    """Single-use async iterator exposing its report after exhaustion."""

    def __init__(self, factory: Callable[[PaginationRun], AsyncIterator[FetchBatch]]) -> None:
        self._factory = factory
        self._started = False
        self._report: CoverageReport | None = None
        self.seen_ids: set[str] = set()
        self.duplicate_hits = 0
        self.missing_object_ids = 0
        self.expected_hits = 0
        self.source_estimated_hits = 0
        self.expected_exhaustive = True
        self.truncated = False
        self.warnings: list[str] = []

    def __aiter__(self) -> AsyncIterator[FetchBatch]:
        if self._started:
            raise RuntimeError("PaginationRun can only be consumed once")
        self._started = True
        return self._factory(self)

    @property
    def report(self) -> CoverageReport:
        if self._report is None:
            raise RuntimeError("Coverage is available after pagination finishes")
        return self._report

    def finish(self) -> None:
        if not self.expected_exhaustive and self.seen_ids and not self.truncated:
            self.warnings.append(
                "Algolia nbHits was non-exhaustive; expected count reconciled from "
                "exhausted ID ranges"
            )
            self.expected_hits = len(self.seen_ids)
        self._report = CoverageReport.calculate(
            expected_hits=self.expected_hits,
            collected_hits=len(self.seen_ids),
            truncated=self.truncated,
            warnings=tuple(self.warnings),
        )


@dataclass(frozen=True, slots=True)
class _Bucket:
    attr_position: int
    lo: int
    hi: int
    filters: tuple[str, ...] = ()


class PaginationPlanner:
    def __init__(self, client: AlgoliaReadClient, *, max_split_depth: int = 64) -> None:
        self._client = client
        self._max_split_depth = max_split_depth

    def fetch(self, spec: PaginationSpec) -> PaginationRun:
        return PaginationRun(lambda run: self._execute(run, spec))

    async def _execute(self, run: PaginationRun, spec: PaginationSpec) -> AsyncIterator[FetchBatch]:
        probe = await self._client.search(
            spec.index_name, replace(spec.query, hits_per_page=0, page=0)
        )
        run.expected_hits = probe.nb_hits
        run.source_estimated_hits = probe.nb_hits
        run.expected_exhaustive = probe.exhaustive_nb_hits
        if probe.nb_hits == 0:
            run.finish()
            return

        strategy = self._strategy(spec)
        if strategy == "browse":
            iterator = self._browse(run, spec)
        elif strategy == "keyset":
            iterator = self._keyset(run, spec)
        else:
            range_attrs = tuple(dict.fromkeys((*spec.secondary_attrs, *spec.key_attrs)))
            iterator = self._range_split(
                run, replace(spec, key_attrs=range_attrs), spec.index_name, spec.query
            )
        remaining = spec.max_hits
        async for batch in iterator:
            if remaining is None:
                yield batch
                continue
            kept = batch.hits[:remaining]
            for hit in batch.hits[remaining:]:
                if hit.object_id is not None:
                    run.seen_ids.discard(hit.object_id)
            remaining -= len(kept)
            if kept:
                yield replace(batch, hits=kept, truncated=remaining == 0)
            if remaining == 0:
                if len(run.seen_ids) < run.expected_hits:
                    run.truncated = True
                    run.warnings.append(f"Bounded collection limit reached ({spec.max_hits} hits)")
                break
        run.finish()

    @staticmethod
    def _strategy(spec: PaginationSpec) -> Literal["browse", "keyset", "range_split"]:
        if spec.strategy != "auto":
            return spec.strategy
        if spec.can_browse:
            return "browse"
        if spec.sorted_index and spec.key_attrs:
            return "keyset"
        return "range_split"

    async def _browse(self, run: PaginationRun, spec: PaginationSpec) -> AsyncIterator[FetchBatch]:
        cursor: str | None = spec.resume_cursor
        while True:
            page = await self._client.browse(
                spec.index_name,
                replace(spec.query, hits_per_page=min(spec.hits_per_page, 1_000), page=0),
                cursor=cursor,
            )
            batch = self._batch(run, page, spec.fetch_tier, page.cursor, strategy="browse")
            if batch is not None:
                yield batch
            if page.cursor is None:
                return
            cursor = page.cursor

    async def _keyset(self, run: PaginationRun, spec: PaginationSpec) -> AsyncIterator[FetchBatch]:
        index_name = spec.sorted_index or spec.index_name
        key_attr = spec.key_attrs[0]
        try:
            cursor: int | None = int(spec.resume_cursor) if spec.resume_cursor else None
        except ValueError:
            cursor = None
        while True:
            filters = list(spec.query.numeric_filters)
            if cursor is not None:
                filters.append(f"{key_attr}<{cursor}")
            base = replace(spec.query, numeric_filters=tuple(filters))
            first = await self._client.search(
                index_name, replace(base, hits_per_page=spec.hits_per_page, page=0)
            )
            if first.nb_hits == 0:
                return
            pages = [first]
            visible_pages = min(
                ceil(min(first.nb_hits, spec.pagination_limit) / spec.hits_per_page),
                ceil(spec.pagination_limit / spec.hits_per_page),
            )
            if visible_pages > 1:
                requests = [
                    AlgoliaRequest(
                        index_name,
                        replace(base, hits_per_page=spec.hits_per_page, page=page_number),
                    )
                    for page_number in range(1, visible_pages)
                ]
                pages.extend(await self._client.multi_query(requests))
            hits = tuple(hit for page in pages for hit in page.hits)
            values = [value for hit in hits if (value := _numeric(hit, key_attr)) is not None]
            if not values:
                run.warnings.append(f"keyset attribute missing: {key_attr}")
                async for batch in self._range_split(run, spec, index_name, base):
                    yield batch
                return
            boundary = min(values)
            above = tuple(hit for hit in hits if (_numeric(hit, key_attr) or boundary) > boundary)
            keyset_batch = self._batch_from_hits(
                run, above, spec.fetch_tier, str(boundary), strategy="keyset"
            )
            if keyset_batch is not None:
                yield keyset_batch
            equality_query = replace(
                spec.query,
                numeric_filters=(*spec.query.numeric_filters, f"{key_attr}={boundary}"),
            )
            tie_spec = replace(
                spec,
                index_name=index_name,
                query=equality_query,
                key_attrs=spec.secondary_attrs,
                sorted_index=None,
                can_browse=False,
                strategy="range_split",
            )
            async for tie_batch in self._range_split(run, tie_spec, index_name, equality_query):
                yield tie_batch
            if first.nb_hits <= spec.pagination_limit:
                return
            cursor = boundary

    async def _range_split(
        self,
        run: PaginationRun,
        spec: PaginationSpec,
        index_name: str,
        base_query: AlgoliaQuery,
    ) -> AsyncIterator[FetchBatch]:
        attrs = spec.key_attrs or spec.secondary_attrs
        completed = _range_completed(spec.resume_cursor)
        if not attrs:
            run.truncated = True
            run.warnings.append("No numeric attribute is available for range splitting")
            return
        lo, hi = _bounds(attrs[0])
        queue: deque[tuple[_Bucket, int]] = deque([(_Bucket(0, lo, hi), 0)])
        while queue:
            window: list[tuple[_Bucket, int]] = []
            while queue and len(window) < 8:
                window.append(queue.popleft())
            probes = await self._client.multi_query(
                [
                    AlgoliaRequest(
                        index_name,
                        replace(
                            base_query,
                            hits_per_page=0,
                            page=0,
                            numeric_filters=(
                                *base_query.numeric_filters,
                                *bucket.filters,
                                f"{attrs[bucket.attr_position]}>={bucket.lo}",
                                f"{attrs[bucket.attr_position]}<{bucket.hi}",
                            ),
                        ),
                    )
                    for bucket, _ in window
                ]
            )
            for (bucket, depth), probe in zip(window, probes, strict=True):
                if probe.nb_hits == 0:
                    continue
                attr = attrs[bucket.attr_position]
                filters = (
                    *base_query.numeric_filters,
                    *bucket.filters,
                    f"{attr}>={bucket.lo}",
                    f"{attr}<{bucket.hi}",
                )
                if probe.nb_hits <= spec.pagination_limit:
                    if not probe.exhaustive_nb_hits:
                        page = await self._client.search(
                            index_name,
                            replace(
                                base_query,
                                hits_per_page=spec.hits_per_page,
                                page=0,
                                numeric_filters=filters,
                            ),
                        )
                        if len(page.hits) < spec.hits_per_page:
                            signature = _range_signature(filters, 0)
                            completed.add(signature)
                            batch = self._batch(
                                run,
                                page,
                                spec.fetch_tier,
                                _range_cursor(completed),
                                strategy="range_split",
                            )
                            if batch is not None:
                                yield batch
                            continue
                    else:
                        async for batch in self._fetch_bounded(
                            run,
                            spec,
                            index_name,
                            base_query,
                            filters,
                            probe.nb_hits,
                            completed=completed,
                        ):
                            yield batch
                        continue
                if bucket.hi - bucket.lo > 1 and depth < self._max_split_depth:
                    (lower_lo, lower_hi), (upper_lo, upper_hi) = bisect_interval(
                        bucket.lo, bucket.hi
                    )
                    lower = _Bucket(bucket.attr_position, lower_lo, lower_hi, bucket.filters)
                    upper = _Bucket(bucket.attr_position, upper_lo, upper_hi, bucket.filters)
                    queue.append((lower, depth + 1))
                    queue.append((upper, depth + 1))
                    continue
                next_position = bucket.attr_position + 1
                if next_position < len(attrs):
                    next_lo, next_hi = _bounds(attrs[next_position])
                    queue.append(
                        (
                            _Bucket(
                                next_position,
                                next_lo,
                                next_hi,
                                (*bucket.filters, f"{attr}>={bucket.lo}", f"{attr}<{bucket.hi}"),
                            ),
                            0,
                        )
                    )
                    continue
                run.truncated = True
                run.warnings.append(
                    f"Bucket {attr}=[{bucket.lo},{bucket.hi}) exceeds pagination limit"
                )
                async for batch in self._fetch_bounded(
                    run,
                    spec,
                    index_name,
                    base_query,
                    filters,
                    spec.pagination_limit,
                    truncated=True,
                    completed=completed,
                ):
                    yield batch

    async def _fetch_bounded(
        self,
        run: PaginationRun,
        spec: PaginationSpec,
        index_name: str,
        base_query: AlgoliaQuery,
        filters: tuple[str, ...],
        expected: int,
        *,
        truncated: bool = False,
        completed: set[str] | None = None,
    ) -> AsyncIterator[FetchBatch]:
        page_count = ceil(min(expected, spec.pagination_limit) / spec.hits_per_page)
        completed_pages = completed if completed is not None else set()
        requests: list[tuple[AlgoliaRequest, str]] = []
        for page_number in range(page_count):
            signature = _range_signature(filters, page_number)
            if signature in completed_pages:
                continue
            requests.append(
                (
                    AlgoliaRequest(
                        index_name,
                        replace(
                            base_query,
                            numeric_filters=filters,
                            hits_per_page=spec.hits_per_page,
                            page=page_number,
                        ),
                    ),
                    signature,
                )
            )
        for offset in range(0, len(requests), 8):
            window = requests[offset : offset + 8]
            pages = await self._client.multi_query([request for request, _ in window])
            for page, (_, signature) in zip(pages, window, strict=True):
                completed_pages.add(signature)
                batch = self._batch(
                    run,
                    page,
                    spec.fetch_tier,
                    _range_cursor(completed_pages),
                    strategy="range_split",
                    truncated=truncated,
                )
                if batch is not None:
                    yield batch

    def _batch(
        self,
        run: PaginationRun,
        page: AlgoliaPage,
        tier: FetchTier,
        cursor: str | None,
        *,
        strategy: str,
        truncated: bool = False,
    ) -> FetchBatch | None:
        return self._batch_from_hits(
            run,
            page.hits,
            tier,
            cursor,
            strategy=strategy,
            truncated=truncated,
        )

    @staticmethod
    def _batch_from_hits(
        run: PaginationRun,
        hits: tuple[dict[str, object], ...] | tuple[dict[str, object | str], ...],
        tier: FetchTier,
        cursor: str | None,
        *,
        strategy: str,
        truncated: bool = False,
    ) -> FetchBatch | None:
        raw_hits: list[RawHit] = []
        for hit in hits:
            raw = RawHit(dict(hit), tier)
            object_id = raw.object_id
            if object_id is None:
                run.missing_object_ids += 1
                run.warnings.append("Hit without objectID was excluded from coverage")
                continue
            if object_id in run.seen_ids:
                run.duplicate_hits += 1
                continue
            run.seen_ids.add(object_id)
            raw_hits.append(raw)
        if not raw_hits:
            return None
        return FetchBatch(
            hits=tuple(raw_hits),
            fetch_tier=tier,
            cursor=cursor,
            truncated=truncated,
            diagnostics={"strategy": strategy},
        )


def _numeric(hit: dict[str, object], attr: str) -> int | None:
    value = hit.get(attr)
    if value is None and not attr.endswith("_i"):
        value = hit.get(f"{attr}_i")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _bounds(attr: str) -> tuple[int, int]:
    lowered = attr.casefold()
    if lowered in {"id", "objectid"}:
        # ponytail: Grailed IDs fit uint31; discover bounds if the source crosses it.
        return 0, 2**31
    if "_at" in lowered or "timestamp" in lowered:
        return 0, 4_102_444_800  # 2100-01-01 UTC
    if "price" in lowered:
        return 0, 100_000_001
    return 0, 2**63


def bisect_interval(lo: int, hi: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """Split a half-open integer range without gaps or boundary overlap."""

    if hi - lo <= 1:
        raise ValueError("Interval is too narrow to bisect")
    middle = lo + (hi - lo) // 2
    return (lo, middle), (middle, hi)


def _range_signature(filters: tuple[str, ...], page: int) -> str:
    return json.dumps([*filters, f"page={page}"], separators=(",", ":"))


def _range_cursor(completed: set[str]) -> str:
    return json.dumps(
        {"strategy": "range_split", "completed": sorted(completed)},
        separators=(",", ":"),
    )


def _range_completed(cursor: str | None) -> set[str]:
    if not cursor or not cursor.startswith("{"):
        return set()
    try:
        payload = json.loads(cursor)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, dict) or payload.get("strategy") != "range_split":
        return set()
    completed = payload.get("completed", [])
    return {str(item) for item in completed} if isinstance(completed, list) else set()
