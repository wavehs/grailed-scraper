"""Add keyset indexes and synchronized listing FTS.

Revision ID: 20260824_0012
Revises: 20260822_0011
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_0012"
down_revision: str | None = "20260822_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_listings_status_id", "listings", ["status", "id"])
    op.drop_index("ix_identity_matches_queue", table_name="identity_matches")
    op.execute(
        "CREATE INDEX ix_identity_matches_queue ON identity_matches "
        "(status, level, confidence DESC, id)"
    )
    op.execute(
        "CREATE INDEX ix_scoring_snapshots_run_demand ON scoring_snapshots "
        "(model_version, parser_run_id, window_days, demand_score DESC, id)"
    )
    op.execute(
        "CREATE INDEX ix_scoring_snapshots_run_demand_scored ON scoring_snapshots "
        "(model_version, parser_run_id, window_days, demand_score DESC, id) "
        "WHERE scoring_status = 'scored'"
    )
    op.execute(
        "CREATE VIRTUAL TABLE listings_fts USING fts5("
        "title, brand_name_raw, content='listings', content_rowid='id', tokenize='unicode61')"
    )
    op.execute(
        "CREATE TRIGGER listings_fts_ai AFTER INSERT ON listings BEGIN "
        "INSERT INTO listings_fts(rowid, title, brand_name_raw) "
        "VALUES (new.id, new.title, new.brand_name_raw); END"
    )
    op.execute(
        "CREATE TRIGGER listings_fts_ad AFTER DELETE ON listings BEGIN "
        "INSERT INTO listings_fts(listings_fts, rowid, title, brand_name_raw) "
        "VALUES ('delete', old.id, old.title, old.brand_name_raw); END"
    )
    op.execute(
        "CREATE TRIGGER listings_fts_au AFTER UPDATE OF title, brand_name_raw ON listings BEGIN "
        "INSERT INTO listings_fts(listings_fts, rowid, title, brand_name_raw) "
        "VALUES ('delete', old.id, old.title, old.brand_name_raw); "
        "INSERT INTO listings_fts(rowid, title, brand_name_raw) "
        "VALUES (new.id, new.title, new.brand_name_raw); END"
    )
    op.execute("INSERT INTO listings_fts(listings_fts) VALUES ('rebuild')")
    op.execute("INSERT INTO listings_fts(listings_fts, rank) VALUES ('integrity-check', 1)")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS listings_fts_au")
    op.execute("DROP TRIGGER IF EXISTS listings_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS listings_fts_ai")
    op.execute("DROP TABLE IF EXISTS listings_fts")
    op.drop_index("ix_scoring_snapshots_run_demand_scored", table_name="scoring_snapshots")
    op.drop_index("ix_scoring_snapshots_run_demand", table_name="scoring_snapshots")
    op.drop_index("ix_identity_matches_queue", table_name="identity_matches")
    op.create_index(
        "ix_identity_matches_queue",
        "identity_matches",
        ["status", "level", "confidence"],
    )
    op.drop_index("ix_listings_status_id", table_name="listings")
