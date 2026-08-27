"""AI grouping persistence must upgrade and downgrade an existing database safely."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db.models import Base


def _config(database_path: Path) -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    return config


def _insert_legacy_assignment(database_path: Path) -> None:
    observed = "2026-08-24T00:00:00+00:00"
    with sqlite3.connect(database_path) as connection:
        brand_id = connection.execute("SELECT id FROM brands ORDER BY id LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO listings "
            "(id, source, grailed_id, status, url, title, brand_name_raw, brand_id, price, "
            "currency_original, likes_count, sold_at_is_estimated, first_seen_at, last_seen_at, "
            "photo_urls, photo_count, seller_identity_mode, quality_flags, fetch_tier, raw_json, "
            "schema_version) VALUES "
            "(1, 'grailed', 1, 'active', 'https://example.test/1', 'Cross Hat', 'Brand', ?, "
            "100, 'USD', 0, 0, ?, ?, '[]', 0, 'none', '[]', 'T1', '{}', 1)",
            (brand_id, observed, observed),
        )
        connection.execute(
            "INSERT INTO model_groups "
            "(id, stable_key, brand_id, name, group_type, created_at, updated_at) "
            "VALUES (1, 'legacy:cross', ?, 'Cross', 'resolved', ?, ?)",
            (brand_id, observed, observed),
        )
        connection.execute(
            "INSERT INTO listing_model_assignments "
            "(listing_id, model_group_id, method, confidence, algorithm_version, updated_at) "
            "VALUES (1, 1, 'exact_line', 1, 'identity-v5', ?)",
            (observed,),
        )
        connection.commit()


def test_ai_grouping_migration_preserves_legacy_assignments_and_is_reversible(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_path = tmp_path / "ai-grouping.db"
    config = _config(database_path)
    command.upgrade(config, "20260824_0013")
    _insert_legacy_assignment(database_path)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()

    command.upgrade(config, "20260824_0014")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert {"ai_grouping_runs", "ai_grouping_batches", "ai_grouping_items"}.issubset(
        inspector.get_table_names()
    )
    assert {"grouping_version", "input_hash", "ai_grouping_run_id"}.issubset(
        {column["name"] for column in inspector.get_columns("listing_model_assignments")}
    )
    assert {
        "ix_listing_model_assignments_grouping_version",
        "ix_listing_model_assignments_input_hash",
        "ix_listing_model_assignments_ai_grouping_run",
    }.issubset({index["name"] for index in inspector.get_indexes("listing_model_assignments")})
    assignment_fk = next(
        foreign_key
        for foreign_key in inspector.get_foreign_keys("listing_model_assignments")
        if foreign_key["constrained_columns"] == ["ai_grouping_run_id"]
    )
    assert assignment_fk["options"].get("ondelete") == "SET NULL"
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT grouping_version FROM listing_model_assignments WHERE listing_id=1"
            ).scalar_one()
            == "legacy"
        )
    engine.dispose()

    with sqlite3.connect(database_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO ai_grouping_runs "
            "(mode, status, base_model, review_model, grouping_version, prompt_version, "
            "budget_limit_usd, actual_cost_usd, input_tokens, output_tokens, total_items, "
            "unique_requests, completed_items, ambiguous_items, failed_items, stats, warnings, "
            "created_at) VALUES "
            "('invalid', 'preparing', 'gemini-2.5-flash-lite', 'gemini-2.5-flash', "
            "'grouping-v1', 'grouping-prompt-v1', 0.5, 0, 0, 0, 0, 0, 0, 0, 0, '{}', '[]', "
            "'2026-08-24T00:00:00+00:00')"
        )

    command.downgrade(config, "20260824_0013")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert not {"ai_grouping_runs", "ai_grouping_batches", "ai_grouping_items"}.intersection(
        inspector.get_table_names()
    )
    assert {"grouping_version", "input_hash", "ai_grouping_run_id"}.isdisjoint(
        {column["name"] for column in inspector.get_columns("listing_model_assignments")}
    )
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT algorithm_version FROM listing_model_assignments WHERE listing_id=1"
            ).scalar_one()
            == "identity-v5"
        )
    engine.dispose()
