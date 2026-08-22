"""The domain migration must create a complete fresh SQLite schema."""

from __future__ import annotations

import json
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_domain_migration_creates_required_tables_and_indexes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    backend_root = __import__("pathlib").Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.db"
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    required_tables = {
        "alembic_version",
        "brands",
        "listings",
        "listing_price_history",
        "fx_rates",
        "source_credentials",
        "source_schema",
        "schema_alerts",
        "parser_runs",
        "parser_run_tasks",
        "parser_watermarks",
        "brand_source_map",
        "unmatched_brands",
        "app_settings",
        "listing_model_assignments",
        "physical_items",
        "physical_item_members",
        "identity_matches",
    }
    assert required_tables.issubset(set(inspector.get_table_names()))
    assert {"ix_listings_brand_status_sold_at", "ix_listings_status_last_seen_at"}.issubset(
        {index["name"] for index in inspector.get_indexes("listings")}
    )
    assert {"color", "source_product_id", "cover_dhash", "identity_version"}.issubset(
        {column["name"] for column in inspector.get_columns("listings")}
    )
    assert {"exact_sold_count", "median_sold_likes", "demand_score", "scoring_status"}.issubset(
        {column["name"] for column in inspector.get_columns("scoring_snapshots")}
    )
    assert "ix_scoring_snapshots_brand_window_demand" in {
        index["name"] for index in inspector.get_indexes("scoring_snapshots")
    }
    parser_run_column = next(
        column for column in inspector.get_columns("listings") if column["name"] == "parser_run_id"
    )
    parser_run_fk = next(
        foreign_key
        for foreign_key in inspector.get_foreign_keys("listings")
        if foreign_key["constrained_columns"] == ["parser_run_id"]
    )
    assert parser_run_column["nullable"] is True
    assert parser_run_fk["options"].get("ondelete") == "SET NULL"
    assert "ix_listing_price_history_listing_observed" in {
        index["name"] for index in inspector.get_indexes("listing_price_history")
    }
    assert "ix_brand_source_map_brand_verified" in {
        index["name"] for index in inspector.get_indexes("brand_source_map")
    }
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM brands").scalar_one() == 21
    engine.dispose()


def test_stage11_migration_moves_hash_and_scrubs_existing_raw_pii(tmp_path) -> None:  # type: ignore[no-untyped-def]
    backend_root = __import__("pathlib").Path(__file__).resolve().parents[1]
    database_path = tmp_path / "privacy-migration.db"
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    command.upgrade(config, "20260808_0006")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO parser_runs "
            "(id, source, mode, status, dry_run, degraded_mode, requests_made, "
            "warnings, stats, created_at, phase) "
            "VALUES (1, 'grailed', 'delta', 'completed', 0, 0, 0, '[]', '{}', ?, 'done')",
            ("2026-08-01T00:00:00+00:00",),
        )
        connection.execute(
            "INSERT INTO listings "
            "(source, grailed_id, status, url, title, brand_name_raw, price, "
            "currency_original, likes_count, sold_at_is_estimated, first_seen_at, "
            "last_seen_at, photo_urls, photo_count, seller_id, seller_username_hash, "
            "quality_flags, fetch_tier, parser_run_id, raw_json, schema_version) "
            "VALUES ('grailed', 7, 'active', 'https://example.test/7', 'Item', 'Brand', "
            "10, 'USD', 0, 0, ?, ?, '[]', 0, 123, ?, '[]', 'T1', 1, ?, 1)",
            (
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
                "a" * 64,
                json.dumps(
                    {
                        "seller": {"id": 123, "username": "alice", "email": "a@b.test"},
                        "user": {"id": 456, "username": "bob"},
                        "location": "Paris, FR",
                        "title": "Item",
                    }
                ),
            ),
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(listings)")}
        row = connection.execute(
            "SELECT seller_identity, seller_identity_mode, raw_json FROM listings"
        ).fetchone()
    assert {"seller_identity", "seller_identity_mode", "raw_json_purged_at"} <= columns
    assert "seller_id" not in columns and "seller_username_hash" not in columns
    assert row is not None and row[0] == "a" * 64 and row[1] == "hashed"
    assert json.loads(row[2]) == {"seller": {}, "user": {}, "title": "Item"}
