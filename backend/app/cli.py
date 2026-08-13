"""Small operational CLI for safe local diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.privacy import require_live_compliance
from app.db.models import Base, Brand, SourceCredential
from app.db.session import get_database_url
from app.services.normalization.mapping import load_source_mapping
from app.services.normalization.normalizer import ListingNormalizer, NormalizationContext
from app.services.operations import backup_database, restore_database, result_dict, retention
from app.services.parser.mock.fixtures import load_catalog, validate_fixture_assets
from app.services.parser.mock.generator import ACTIVE_INDEX, BRANDS, SOLD_INDEX
from app.services.sources.base.models import RawHit
from app.services.sources.grailed.algolia.client import AlgoliaClient
from app.services.sources.grailed.algolia.models import AlgoliaQuery
from app.services.transport.capabilities import probe_capabilities
from app.services.transport.factory import create_http_transport
from app.services.transport.mock_http import MOCK_ALGOLIA_BASE_URL, MockHttpTransport
from app.services.transport.protocols import HttpTransport


@dataclass(frozen=True, slots=True)
class SeedResult:
    inserted: int
    updated: int


async def seed_mock_brands(database_url: str) -> SeedResult:
    """Idempotently load the curated product-brand seeds into a SQLite database."""

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    inserted = 0
    updated = 0
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            for brand in BRANDS:
                aliases = list(dict.fromkeys((*brand.aliases, brand.designer_name)))
                existing = await session.scalar(select(Brand).where(Brand.name == brand.name))
                if existing is None:
                    session.add(
                        Brand(
                            name=brand.name,
                            slug=brand.slug,
                            aliases=aliases,
                            include_subbrands=False,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    inserted += 1
                elif existing.slug != brand.slug or existing.aliases != aliases:
                    existing.slug = brand.slug
                    existing.aliases = aliases
                    existing.updated_at = now
                    updated += 1
            await session.commit()
    finally:
        await engine.dispose()
    return SeedResult(inserted=inserted, updated=updated)


async def replay_mock_smoke() -> dict[str, object]:
    """Run fixture requests through T0 without opening a network socket."""

    fixture = validate_fixture_assets()
    catalog = load_catalog()
    transport = MockHttpTransport(catalog=catalog)
    requests = 0
    try:
        search = await transport.request(
            "POST",
            f"{MOCK_ALGOLIA_BASE_URL}/1/indexes/{SOLD_INDEX}/query",
            json_body={"params": "query=Rick+Owens&hitsPerPage=2&page=0"},
        )
        assert search.status_code == 200 and len(search.json()["hits"]) == 2
        requests += 1

        multi = await transport.request(
            "POST",
            f"{MOCK_ALGOLIA_BASE_URL}/1/indexes/*/queries",
            json_body={
                "requests": [
                    {"indexName": SOLD_INDEX, "params": "query=Kapital&hitsPerPage=1"},
                    {"indexName": SOLD_INDEX, "params": "query=Visvim&hitsPerPage=1"},
                ]
            },
        )
        assert multi.status_code == 200 and len(multi.json()["results"]) == 2
        requests += 1

        browse = await transport.request(
            "POST",
            f"{MOCK_ALGOLIA_BASE_URL}/1/indexes/{SOLD_INDEX}/browse",
            json_body={"params": "hitsPerPage=1"},
        )
        assert browse.status_code == 200 and browse.json().get("cursor")
        requests += 1

        facets = await transport.request(
            "POST",
            f"{MOCK_ALGOLIA_BASE_URL}/1/indexes/{SOLD_INDEX}/facets/designers.name/query",
            json_body={"facetQuery": "Rick Owens"},
        )
        assert facets.status_code == 200 and facets.json()["facetHits"]
        requests += 1

        key = await transport.request(
            "GET", f"{MOCK_ALGOLIA_BASE_URL}/1/keys/fixture-key"
        )
        assert key.status_code == 200 and "browse" in key.json()["acl"]
        requests += 1
    finally:
        await transport.close()
    return {"status": "ok", "requests": requests, "fixture": fixture}


@dataclass(frozen=True, slots=True)
class _CanaryCredentials:
    app_id: str
    api_key: str
    algolia_agent: str | None = None
    session_headers: tuple[tuple[str, str], ...] = ()


async def run_canary(settings: Settings, brand: str, limit: int) -> dict[str, object]:
    """Fetch and normalize a bounded compatibility sample without persistence."""

    require_live_compliance(settings)
    mock = settings.source_mode in {"mock", "replay"}
    engine = None
    transport: HttpTransport
    if mock:
        transport = MockHttpTransport()
        credentials = _CanaryCredentials("fixture-app", "fixture-key", "fixture-agent")
        index_name = ACTIVE_INDEX
    else:
        engine = create_async_engine(get_database_url(settings))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            cached = await session.scalar(
                select(SourceCredential).where(SourceCredential.source == "grailed")
            )
        if cached is None or cached.active_index is None:
            await engine.dispose()
            raise RuntimeError("Run discovery refresh before a live canary")
        transport = create_http_transport(settings)
        credentials = _CanaryCredentials(cached.app_id, cached.api_key, cached.algolia_agent)
        index_name = cached.active_index
    client = AlgoliaClient(
        transport,
        credentials,
        mock=mock,
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
            ),
        )
        normalizer = ListingNormalizer(load_source_mapping(), settings=settings)
        observed = datetime.now(UTC)
        valid = rejected = 0
        for payload in page.hits[:limit]:
            result = await normalizer.normalize(
                RawHit(dict(payload), "T0" if mock else "T1"),
                NormalizationContext(
                    status="active",
                    parser_run_id=1,
                    observed_at=observed,
                    fetch_tier="T0" if mock else "T1",
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
        if engine is not None:
            await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="show Scraping stack capability report")
    seed_parser = subparsers.add_parser("seed", help="seed the 21 deterministic mock brands")
    seed_parser.add_argument("--database-url", help="override APP_DATABASE_URL for this command")
    subparsers.add_parser("replay", help="run the offline fake-Algolia smoke transcript")
    canary_parser = subparsers.add_parser(
        "canary", help="run a bounded source compatibility sample"
    )
    canary_parser.add_argument("--brand", required=True)
    canary_parser.add_argument("--limit", type=int, default=50, choices=range(1, 201))
    retention_parser = subparsers.add_parser(
        "retention", help="preview or apply raw-data and backup retention"
    )
    retention_parser.add_argument("--apply", action="store_true")
    backup_parser = subparsers.add_parser("db-backup", help="create a verified SQLite backup")
    backup_parser.add_argument("--destination", type=Path)
    restore_parser = subparsers.add_parser("db-restore", help="verify or restore SQLite backup")
    restore_parser.add_argument("source", type=Path)
    restore_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        print(json.dumps(probe_capabilities().as_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "seed":
        settings = Settings(database_url=args.database_url) if args.database_url else Settings()
        fixture = validate_fixture_assets()
        result = asyncio.run(seed_mock_brands(get_database_url(settings)))
        print(
            json.dumps(
                {
                    "status": "ok",
                    "inserted": result.inserted,
                    "updated": result.updated,
                    "fixture": fixture,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "replay":
        print(json.dumps(asyncio.run(replay_mock_smoke()), indent=2, sort_keys=True))
        return 0
    if args.command == "canary":
        try:
            canary_result = asyncio.run(run_canary(Settings(), args.brand, args.limit))
        except RuntimeError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}))
            return 1
        print(json.dumps(canary_result, indent=2, sort_keys=True))
        return 0 if canary_result["status"] == "ok" else 1
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
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
