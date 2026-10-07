"""Small operational CLI for safe local diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Coroutine, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import (
    Brand,
    Listing,
    ListingModelAssignment,
    ModelGroup,
    SourceCredential,
)
from app.db.session import get_database_url
from app.services.grouping import GroupingService, evaluation
from app.services.grouping.policy import load_policy
from app.services.grouping.scratch import make_scratch
from app.services.metrics import MetricsService
from app.services.normalization.mapping import load_source_mapping
from app.services.normalization.normalizer import ListingNormalizer, NormalizationContext
from app.services.operations import (
    backup_database,
    restore_database,
    result_dict,
    retention,
    sqlite_path,
)
from app.services.parser.observability import RunMetrics
from app.services.parser.planner import collection_filters
from app.services.sources.base.models import RawHit
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.models import AlgoliaCredentialsData, AlgoliaQuery
from app.services.sources.grailed.algolia.pagination import PaginationPlanner, PaginationSpec
from app.services.sources.grailed.discovery.service import DiscoveryService
from app.services.transport.factory import create_http_transport
from app.services.transport.protocols import HttpTransport


async def run_canary(settings: Settings, brand: str, limit: int) -> dict[str, object]:
    """Fetch and normalize a bounded compatibility sample without persistence."""

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
                numeric_filters=collection_filters(settings, "active"),
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
                        numeric_filters=collection_filters(settings, index_type),
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


async def run_discovery(settings: Settings) -> dict[str, object]:
    """Read the public search key from Grailed's page config and probe indices and facets."""

    engine = create_async_engine(get_database_url(settings))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    transport: HttpTransport = create_http_transport(settings)
    try:
        async with factory() as session:
            result = await DiscoveryService(session, settings, transport).refresh(force=True)
            await session.commit()
        return {
            "status": result.status,
            "method": result.method,
            "active_index": result.active_index,
            "sold_index": result.sold_index,
            "brand_facet": result.brand_facet,
            "category_facet": result.category_facet,
            "max_hits_per_page": result.max_hits_per_page,
            "schema_field_count": result.schema_field_count,
            "credentials_included": False,
        }
    finally:
        await transport.close()
        await engine.dispose()


async def run_taxonomy_check(settings: Settings) -> dict[str, object]:
    """One bounded request: real category_path values that config/taxonomy.yaml lacks."""

    engine = create_async_engine(get_database_url(settings))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        cached = await session.scalar(
            select(SourceCredential).where(SourceCredential.source == "grailed")
        )
    await engine.dispose()
    if cached is None or cached.active_index is None:
        raise RuntimeError("Run discovery before the taxonomy check")
    transport: HttpTransport = create_http_transport(settings)
    client = AlgoliaClient(
        transport,
        AlgoliaCredentialsData(cached.app_id, cached.api_key, cached.algolia_agent),
        requests_per_minute=settings.requests_per_minute,
        max_concurrency=1,
        max_retries=settings.parser_max_retries,
        timeout_s=settings.parser_request_timeout_s,
    )
    facet = cached.category_facet or "category_path"
    try:
        page = await client.search(
            cached.active_index,
            AlgoliaQuery(hits_per_page=0, facets=(facet,), extra={"maxValuesPerFacet": 1000}),
        )
    finally:
        await transport.close()
    values = page.facets.get(facet, {})
    taxonomy = load_policy().taxonomy
    unknown = {
        path: count
        for path, count in sorted(values.items(), key=lambda item: -item[1])
        if not taxonomy.rule_for(path)[1]
    }
    return {
        "status": "ok" if values and not unknown else "partial" if values else "failed",
        "facet": facet,
        "category_paths": len(values),
        "mapped": len(values) - len(unknown),
        "unknown": unknown,
    }


async def regroup(settings: Settings, *, full: bool = True) -> dict[str, object]:
    """Back up, then regroup every brand and recompute all group metrics."""

    backup = backup_database(settings)
    engine = create_async_engine(get_database_url(settings))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await GroupingService(session).regroup(full=full)
            await session.commit()
            metrics = await MetricsService(session).recompute()
            await session.commit()
        return {
            "status": "ok",
            "backup": str(backup),
            "grouping": result.summary(),
            "metrics": metrics.summary(),
        }
    finally:
        await engine.dispose()


async def grouping_report(settings: Settings, brand: str | None) -> dict[str, object]:
    """Offline grouping quality: model coverage, mixed-type check and top groups per brand."""

    engine = create_async_engine(get_database_url(settings))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            statement = select(Brand).order_by(Brand.name)
            if brand:
                statement = statement.where(func.lower(Brand.name) == brand.casefold())
            brands = list(await session.scalars(statement))
            report: dict[str, object] = {}
            for item in brands:
                rows = (
                    await session.execute(
                        select(
                            ModelGroup.id,
                            ModelGroup.name,
                            ModelGroup.slug,
                            ModelGroup.product_type,
                            ModelGroup.status,
                            func.count(ListingModelAssignment.listing_id),
                        )
                        .join(
                            ListingModelAssignment,
                            ListingModelAssignment.model_group_id == ModelGroup.id,
                        )
                        .where(ModelGroup.brand_id == item.id)
                        .group_by(ModelGroup.id)
                    )
                ).all()
                total = sum(row[5] for row in rows)
                none = sum(row[5] for row in rows if row[2] == "_none" and row[3] != "review")
                review = sum(row[5] for row in rows if row[3] == "review")
                mixed = await session.scalar(
                    select(func.count())
                    .select_from(Listing)
                    .join(
                        ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id
                    )
                    .join(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
                    .where(
                        Listing.brand_id == item.id,
                        Listing.product_type != ModelGroup.product_type,
                    )
                )
                top = sorted(
                    (row for row in rows if row[2] != "_none"), key=lambda row: -row[5]
                )[:15]
                report[item.name] = {
                    "listings": total,
                    "with_model": total - none - review,
                    "with_model_share": round((total - none - review) / total, 3) if total else 0,
                    "no_model": none,
                    "review": review,
                    "mixed_type_assignments": int(mixed or 0),
                    "top_groups": [
                        {"name": row[1], "type": row[3], "status": row[4], "listings": row[5]}
                        for row in top
                    ],
                }
        return {"status": "ok", "brands": report}
    finally:
        await engine.dispose()


def _read_only_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///file:{path.resolve().as_posix()}?mode=ro&uri=true"


async def _snapshot(path: Path, brand: str) -> evaluation.Snapshot:
    if not path.is_file():
        raise RuntimeError(f"Database {path} does not exist")
    engine = create_async_engine(_read_only_url(path))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await evaluation.load_snapshot(session, brand)
    finally:
        await engine.dispose()


def _eval_directory(brand: str, directory: Path | None) -> Path:
    path = directory or evaluation.EVAL_DIRECTORY / brand
    if not path.is_dir():
        raise RuntimeError(f"Eval directory {path} does not exist")
    return path


async def grouping_eval(
    settings: Settings,
    *,
    brand: str,
    split: evaluation.Split,
    checkpoint: str | None,
    strict: bool,
    baseline_db: Path | None,
    directory: Path | None = None,
) -> dict[str, object]:
    """Read-only: measure the database's grouping against the labels in eval/<brand>/."""

    folder = _eval_directory(brand, directory)
    config = evaluation.load_config(folder)
    current = await _snapshot(sqlite_path(settings), brand)
    baseline = await _snapshot(baseline_db, brand) if baseline_db else None
    try:
        report = evaluation.evaluate(
            config=config,
            directory=folder,
            current=current,
            baseline=baseline,
            split=split,
            checkpoint=checkpoint,
            strict=strict,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    result = evaluation.jsonable(report)
    result["status"] = "ok" if report["passed"] else "failed"
    return dict(result)


async def grouping_baseline_report(
    settings: Settings,
    *,
    brand: str,
    scratch_db: Path | None,
    directory: Path | None = None,
) -> dict[str, object]:
    """Read-only step-0 numbers; a from-scratch regroup runs on a light copy, not the database."""

    folder = _eval_directory(brand, directory)
    config = evaluation.load_config(folder)
    source = sqlite_path(settings)
    current = await _snapshot(source, brand)
    if scratch_db is None:
        scratch_db = source.parent / "cache" / "eval" / f"scratch-{brand}.db"
        make_scratch(source, scratch_db, brand, keep_auto=False)
        engine = create_async_engine(f"sqlite+aiosqlite:///{scratch_db.as_posix()}")
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await GroupingService(session).regroup(full=True)
                await session.commit()
        finally:
            await engine.dispose()
    scratch = await _snapshot(scratch_db, brand)
    report = evaluation.baseline_report(
        current,
        scratch,
        evaluation.load_phrases(folder / "phrases.csv"),
        config,
        evaluation.load_codesigners(folder / "codesigners.csv"),
    )
    return {"status": "ok", "scratch_db": str(scratch_db), **evaluation.jsonable(report)}


async def grouping_eval_sample(
    settings: Settings,
    *,
    brand: str,
    out: Path,
    sheets: Sequence[str],
    sold: int,
    active: int,
    top: int,
    directory: Path | None = None,
) -> dict[str, object]:
    """Write blind labeling sheets (no current groups, ranks or counts) into ``out``."""

    folder = _eval_directory(brand, directory)
    config = evaluation.load_config(folder)
    snapshot = await _snapshot(sqlite_path(settings), brand)
    labels_path = folder / "labels.csv"
    labels = evaluation.load_labels(labels_path) if labels_path.is_file() else []
    verdicts = evaluation.load_phrases(folder / "phrases.csv")
    lines = evaluation.rank_lines(snapshot)
    written: dict[str, int] = {}
    if "listings" in sheets:
        rows = evaluation.listing_sheet(
            snapshot, sold=sold, active=active, exclude=[label.grailed_id for label in labels]
        )
        written["listings.csv"] = evaluation.write_sheet(
            out / "listings.csv", evaluation.SHEET_LISTING_COLUMNS, rows
        )
    if "phrases" in sheets:
        tail = evaluation.tail_sample(
            lines, after=config.tail_after, size=config.tail_size, seed=config.tail_seed
        )
        wanted = [*lines[:top], *tail]
        rows = evaluation.phrase_sheet(
            snapshot, wanted, exclude=verdicts.keys(), seed=config.tail_seed
        )
        written["phrases.csv"] = evaluation.write_sheet(
            out / "phrases.csv", evaluation.SHEET_PHRASE_COLUMNS, rows
        )
    if "audit" in sheets:
        listing_rows, phrase_rows = evaluation.audit_sheets(
            snapshot, lines, labels, verdicts, seed=config.tail_seed + 1
        )
        written["audit_listings.csv"] = evaluation.write_sheet(
            out / "audit_listings.csv", evaluation.SHEET_LISTING_COLUMNS, listing_rows
        )
        written["audit_phrases.csv"] = evaluation.write_sheet(
            out / "audit_phrases.csv", evaluation.SHEET_PHRASE_COLUMNS, phrase_rows
        )
    return {"status": "ok", "out": str(out), "written": written}


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="show HTTP stack versions (no network)")
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
    subparsers.add_parser(
        "discover", help="refresh the public search key and indices (live, bounded)"
    )
    subparsers.add_parser(
        "taxonomy-check", help="list real category paths missing from config/taxonomy.yaml"
    )
    regroup_parser = subparsers.add_parser(
        "regroup", help="back up, regroup all brands and recompute metrics"
    )
    regroup_parser.add_argument(
        "--delta", action="store_true", help="only new or changed listings when rules are unchanged"
    )
    report_parser = subparsers.add_parser(
        "grouping-report", help="grouping quality per brand (offline)"
    )
    report_parser.add_argument("--brand")
    eval_parser = subparsers.add_parser(
        "grouping-eval", help="measure grouping against eval/<brand>/ labels (read-only)"
    )
    eval_parser.add_argument("--brand", required=True, help="brand slug, e.g. balenciaga")
    eval_parser.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    eval_parser.add_argument("--checkpoint", help="checkpoint name from eval.yaml")
    eval_parser.add_argument(
        "--strict", action="store_true", help="fail on warnings (unlabeled top lines etc.)"
    )
    eval_parser.add_argument(
        "--baseline-db", type=Path, help="database before the change: regression, lost groups"
    )
    eval_parser.add_argument("--eval-dir", type=Path)
    eval_parser.add_argument(
        "--seal", action="store_true", help="record label sha256 in MANIFEST and exit"
    )
    eval_parser.add_argument("--labeler", default="")
    eval_parser.add_argument("--external-check", default="none")
    eval_parser.add_argument("--audit-listings", type=Path, help="filled audit listing sheet")
    eval_parser.add_argument("--audit-phrases", type=Path, help="filled audit phrase sheet")
    eval_parser.add_argument(
        "--baseline-report", action="store_true", help="step-0 numbers with their formulas"
    )
    eval_parser.add_argument(
        "--scratch-db", type=Path, help="regrouped from-scratch copy (default: build one)"
    )
    sample_parser = subparsers.add_parser(
        "grouping-eval-sample", help="blind labeling sheets without current groups"
    )
    sample_parser.add_argument("--brand", required=True, help="brand slug, e.g. balenciaga")
    sample_parser.add_argument("--out", type=Path, help="directory for the sheets")
    sample_parser.add_argument(
        "--sheet",
        action="append",
        choices=("listings", "phrases", "audit"),
        help="sheets to write (default: listings and phrases)",
    )
    sample_parser.add_argument("--sold", type=int, default=1200)
    sample_parser.add_argument("--active", type=int, default=300)
    sample_parser.add_argument("--top", type=int, default=200)
    sample_parser.add_argument(
        "--import", dest="import_sheet", type=Path, help="merge a filled sheet into the labels"
    )
    sample_parser.add_argument("--eval-dir", type=Path)
    args = parser.parse_args()
    if args.command == "doctor":
        from importlib import metadata

        report = {name: metadata.version(name) for name in ("curl_cffi", "fastapi", "SQLAlchemy")}
        print(json.dumps(report, indent=2, sort_keys=True))
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
    if args.command == "discover":
        return _print(run_discovery(Settings()))
    if args.command == "taxonomy-check":
        return _print(run_taxonomy_check(Settings()))
    if args.command == "regroup":
        return _print(regroup(Settings(), full=not args.delta))
    if args.command == "grouping-report":
        return _print(grouping_report(Settings(), args.brand))
    if args.command == "grouping-eval":
        folder = args.eval_dir or evaluation.EVAL_DIRECTORY / args.brand
        if args.seal:
            sealed = evaluation.seal_manifest(
                folder, labeler=args.labeler, external_check=args.external_check
            )
            return _dump({"status": "ok", **sealed})
        if args.baseline_report:
            return _print(
                grouping_baseline_report(
                    Settings(),
                    brand=args.brand,
                    scratch_db=args.scratch_db,
                    directory=args.eval_dir,
                )
            )
        if args.audit_listings or args.audit_phrases:
            agreement = evaluation.audit_agreement(folder, args.audit_listings, args.audit_phrases)
            return _dump({"status": "ok", **evaluation.jsonable(agreement)})
        return _print(
            grouping_eval(
                Settings(),
                brand=args.brand,
                split=args.split,
                checkpoint=args.checkpoint,
                strict=args.strict,
                baseline_db=args.baseline_db,
                directory=args.eval_dir,
            )
        )
    if args.command == "grouping-eval-sample":
        folder = args.eval_dir or evaluation.EVAL_DIRECTORY / args.brand
        if args.import_sheet:
            return _dump({"status": "ok", **evaluation.import_sheet(folder, args.import_sheet)})
        if args.out is None:
            parser.error("grouping-eval-sample needs --out or --import")
        return _print(
            grouping_eval_sample(
                Settings(),
                brand=args.brand,
                out=args.out,
                sheets=args.sheet or ("listings", "phrases"),
                sold=args.sold,
                active=args.active,
                top=args.top,
                directory=args.eval_dir,
            )
        )
    parser.error("Unknown command")
    return 2


def _dump(result: dict[str, Any]) -> int:
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


def _print(job: Coroutine[Any, Any, dict[str, object]]) -> int:
    try:
        result = asyncio.run(job)
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0 if result.get("status") in {"ok", "ready", "degraded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
