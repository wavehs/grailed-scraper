"""Small operational CLI for safe local diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.privacy import require_live_compliance
from app.db.models import (
    Listing,
    ListingModelAssignment,
    ParserRun,
    ScoringSnapshot,
    SourceCredential,
)
from app.db.session import get_database_url
from app.services.identity import IdentityResolver
from app.services.identity.service import IDENTITY_VERSION
from app.services.normalization.mapping import load_source_mapping
from app.services.normalization.normalizer import ListingNormalizer, NormalizationContext
from app.services.operations import backup_database, restore_database, result_dict, retention
from app.services.parser.observability import RunMetrics
from app.services.parser.planner import listing_numeric_filters
from app.services.scoring import MODEL_VERSION, OpportunityScoringService
from app.services.sources.base.models import RawHit
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.models import AlgoliaCredentialsData, AlgoliaQuery
from app.services.sources.grailed.algolia.pagination import PaginationPlanner, PaginationSpec
from app.services.transport.capabilities import probe_capabilities
from app.services.transport.factory import create_http_transport
from app.services.transport.protocols import HttpTransport


async def run_canary(settings: Settings, brand: str, limit: int) -> dict[str, object]:
    """Fetch and normalize a bounded compatibility sample without persistence."""

    require_live_compliance(settings)
    engine = create_async_engine(get_database_url(settings))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        cached = await session.scalar(
            select(SourceCredential).where(SourceCredential.source == "grailed")
        )
    if cached is None or cached.active_index is None:
        await engine.dispose()
        raise RuntimeError("Run discovery refresh before a live canary")
    transport: HttpTransport = create_http_transport(settings)
    credentials = AlgoliaCredentialsData(cached.app_id, cached.api_key, cached.algolia_agent)
    index_name = cached.active_index
    client = AlgoliaClient(
        transport,
        credentials,
        requests_per_minute=settings.requests_per_minute,
        max_concurrency=min(settings.max_concurrent_requests, 3),
        max_retries=settings.parser_max_retries,
        timeout_s=settings.parser_request_timeout_s,
    )
    try:
        page = await client.search(
            index_name,
            AlgoliaQuery(
                hits_per_page=limit,
                facet_filters=((f"designers.name:{brand}",),),
                numeric_filters=listing_numeric_filters(),
            ),
        )
        normalizer = ListingNormalizer(load_source_mapping(), settings=settings)
        observed = datetime.now(UTC)
        valid = rejected = 0
        for payload in page.hits[:limit]:
            result = await normalizer.normalize(
                RawHit(dict(payload), "T1"),
                NormalizationContext(
                    status="active",
                    parser_run_id=1,
                    observed_at=observed,
                    fetch_tier="T1",
                ),
            )
            valid += int(result.valid)
            rejected += int(not result.valid)
        return {
            "status": "ok" if valid else "failed",
            "source_mode": settings.source_mode,
            "brand": brand,
            "limit": limit,
            "fetched": min(len(page.hits), limit),
            "valid": valid,
            "rejected": rejected,
            "index": index_name,
        }
    finally:
        await transport.close()
        await engine.dispose()


async def run_collection_canary(settings: Settings, brand: str) -> dict[str, object]:
    """Collect one live brand without persisting listing payloads."""

    require_live_compliance(settings)
    engine = create_async_engine(get_database_url(settings))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        cached = await session.scalar(
            select(SourceCredential).where(SourceCredential.source == "grailed")
        )
    if cached is None or cached.active_index is None or cached.sold_index is None:
        await engine.dispose()
        raise RuntimeError("Run discovery refresh before a live collection canary")
    transport: HttpTransport = create_http_transport(settings)
    metrics = RunMetrics()
    client = AlgoliaClient(
        transport,
        AlgoliaCredentialsData(cached.app_id, cached.api_key, cached.algolia_agent),
        requests_per_minute=settings.requests_per_minute,
        max_concurrency=min(settings.max_concurrent_requests, 3),
        max_retries=settings.parser_max_retries,
        multiquery_batch_size=settings.algolia_multiquery_batch_size,
        timeout_s=settings.parser_request_timeout_s,
        metrics=metrics,
    )
    can_browse = "browse" in cached.key_acl.get("acl", [])
    sorted_indices = list(cached.sorted_indices)
    facet = cached.brand_facet or "designers.name"
    page_size = min(settings.algolia_hits_per_page, cached.max_hits_per_page or 1_000)
    reports: dict[str, object] = {}
    try:
        for index_type, index_name, key_attrs in (
            ("active", cached.active_index, ("created_at_i", "created_at", "id")),
            ("sold", cached.sold_index, ("sold_at_i", "sold_at", "created_at_i", "id")),
        ):
            token = "sold" if index_type == "sold" else "date"
            sorted_index = next((name for name in sorted_indices if token in name.casefold()), None)
            pagination = PaginationPlanner(client).fetch(
                PaginationSpec(
                    index_name=index_name,
                    query=AlgoliaQuery(
                        hits_per_page=page_size,
                        facet_filters=((f"{facet}:{brand}",),),
                        numeric_filters=listing_numeric_filters(),
                    ),
                    strategy=settings.algolia_pagination_strategy,
                    can_browse=can_browse,
                    sorted_index=sorted_index,
                    key_attrs=key_attrs,
                    pagination_limit=cached.pagination_limit or 1_000,
                    hits_per_page=page_size,
                )
            )
            async for _ in pagination:
                pass
            report = pagination.report
            selected_strategy = settings.algolia_pagination_strategy
            if selected_strategy == "auto":
                selected_strategy = (
                    "browse" if can_browse else "keyset" if sorted_index else "range_split"
                )
            reports[index_type] = {
                "index": index_name,
                "strategy": selected_strategy,
                "source_estimated_hits": pagination.source_estimated_hits,
                "source_estimate_exhaustive": pagination.expected_exhaustive,
                "expected": report.expected_hits,
                "collected_unique": report.collected_hits,
                "duplicates_removed": pagination.duplicate_hits,
                "duplicates_in_output": 0,
                "missing_object_ids": pagination.missing_object_ids,
                "coverage": str(report.coverage) if report.coverage is not None else None,
                "coverage_status": report.status,
                "truncated": report.truncated,
                "warnings": list(report.warnings),
            }
        complete = all(
            isinstance(report, dict)
            and report["coverage_status"] in {"complete", "skipped"}
            and report["duplicates_in_output"] == 0
            and report["missing_object_ids"] == 0
            for report in reports.values()
        )
        metric_snapshot = metrics.snapshot()
        metric_snapshot.pop("_latency_samples_ms", None)
        return {
            "status": "ok" if complete else "partial",
            "source_mode": settings.source_mode,
            "brand": brand,
            "tier": "T1",
            "reports": reports,
            "metrics": metric_snapshot,
            "credentials_included": False,
            "seller_pii_included": False,
        }
    finally:
        await transport.close()
        await engine.dispose()


async def rebuild_market(settings: Settings, *, resume: bool = False) -> dict[str, object]:
    """Back up and rebuild current model identity and market snapshots."""

    backup = backup_database(settings)
    engine = create_async_engine(get_database_url(settings))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    transport: HttpTransport = create_http_transport(settings)
    identity_settings = settings.model_copy(update={"identity_image_requests_per_run": 0})
    try:
        async with factory() as session:
            run_id = await session.scalar(
                select(func.max(ParserRun.id)).where(
                    ParserRun.status.in_(("completed", "partial"))
                )
            )
            if run_id is None:
                raise RuntimeError("No completed or partial parser run exists")
            before = int(
                await session.scalar(
                    select(func.count(func.distinct(ListingModelAssignment.model_group_id)))
                )
                or 0
            )
            all_brand_ids = {
                brand_id
                for brand_id in await session.scalars(
                    select(Listing.brand_id).where(Listing.brand_id.is_not(None)).distinct()
                )
                if brand_id is not None
            }
            if not resume:
                await session.execute(
                    update(ListingModelAssignment).values(algorithm_version="stale")
                )
                await session.commit()
            stale_brand_ids = {
                brand_id
                for brand_id in await session.scalars(
                    select(Listing.brand_id)
                    .outerjoin(
                        ListingModelAssignment,
                        ListingModelAssignment.listing_id == Listing.id,
                    )
                    .where(
                        Listing.brand_id.is_not(None),
                        or_(
                            ListingModelAssignment.listing_id.is_(None),
                            ListingModelAssignment.algorithm_version != IDENTITY_VERSION,
                        ),
                    )
                    .distinct()
                )
                if brand_id is not None
            }
            brand_ids = stale_brand_ids if resume else all_brand_ids
        identity_by_brand: dict[int, dict[str, int | str]] = {}
        for brand_id in sorted(brand_ids):
            async with factory() as session:
                identity_by_brand[brand_id] = await IdentityResolver(
                    session, identity_settings, transport
                ).resolve_run(
                    run_id,
                    brand_ids={brand_id},
                    rebuild_all_physical=True,
                )
                await session.commit()
        async with factory() as session:
            after = int(
                await session.scalar(
                    select(func.count(func.distinct(ListingModelAssignment.model_group_id)))
                )
                or 0
            )
            await session.execute(
                delete(ScoringSnapshot).where(
                    ScoringSnapshot.parser_run_id == run_id,
                    ScoringSnapshot.model_version == MODEL_VERSION,
                )
            )
            await session.commit()
        scoring = await OpportunityScoringService(factory).score_run(
            run_id, brand_ids=all_brand_ids
        )
        async with factory() as session:
            insufficient = int(
                await session.scalar(
                    select(func.count(ScoringSnapshot.id)).where(
                        ScoringSnapshot.parser_run_id == run_id,
                        ScoringSnapshot.model_version == MODEL_VERSION,
                        ScoringSnapshot.scoring_status != "scored",
                    )
                )
                or 0
            )
        return {
            "status": "ok",
            "backup": str(backup),
            "run_id": run_id,
            "groups_before": before,
            "groups_after": after,
            "insufficient_snapshots": insufficient,
            "identity": identity_by_brand,
            "scoring": scoring,
        }
    finally:
        await transport.close()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="show Scraping stack capability report")
    canary_parser = subparsers.add_parser(
        "canary", help="run a bounded source compatibility sample"
    )
    canary_parser.add_argument("--brand", required=True)
    canary_parser.add_argument("--limit", type=int, default=50, choices=range(1, 201))
    collection_parser = subparsers.add_parser(
        "collect-brand", help="collect one complete live brand without persistence"
    )
    collection_parser.add_argument("--brand", required=True)
    retention_parser = subparsers.add_parser(
        "retention", help="preview or apply raw-data and backup retention"
    )
    retention_parser.add_argument("--apply", action="store_true")
    backup_parser = subparsers.add_parser("db-backup", help="create a verified SQLite backup")
    backup_parser.add_argument("--destination", type=Path)
    restore_parser = subparsers.add_parser("db-restore", help="verify or restore SQLite backup")
    restore_parser.add_argument("source", type=Path)
    restore_parser.add_argument("--apply", action="store_true")
    rebuild_parser = subparsers.add_parser(
        "market-rebuild", help="back up and rebuild identity-v5 and market-v5"
    )
    rebuild_parser.add_argument(
        "--resume", action="store_true", help="skip brands already assigned by identity-v5"
    )
    args = parser.parse_args()
    if args.command == "doctor":
        print(json.dumps(probe_capabilities().as_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "canary":
        try:
            canary_result = asyncio.run(run_canary(Settings(), args.brand, args.limit))
        except RuntimeError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}))
            return 1
        print(json.dumps(canary_result, indent=2, sort_keys=True))
        return 0 if canary_result["status"] == "ok" else 1
    if args.command == "collect-brand":
        try:
            collection_result = asyncio.run(run_collection_canary(Settings(), args.brand))
        except RuntimeError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}))
            return 1
        print(json.dumps(collection_result, indent=2, sort_keys=True))
        return 0 if collection_result["status"] == "ok" else 1
    if args.command == "retention":
        print(json.dumps(result_dict(retention(Settings(), apply=args.apply)), indent=2))
        return 0
    if args.command == "db-backup":
        target = backup_database(Settings(), destination=args.destination)
        print(json.dumps({"status": "ok", "backup": str(target)}, indent=2))
        return 0
    if args.command == "db-restore":
        print(json.dumps(restore_database(Settings(), args.source, apply=args.apply), indent=2))
        return 0
    if args.command == "market-rebuild":
        try:
            rebuild_result = asyncio.run(rebuild_market(Settings(), resume=args.resume))
        except RuntimeError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}))
            return 1
        print(json.dumps(rebuild_result, indent=2, sort_keys=True))
        return 0
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
