"""Restrict fetch tiers to direct Algolia (T1); browser tiers were removed.

Revision ID: 20261006_0017
Revises: 20261006_0016
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0017"
down_revision: str | None = "20261006_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LISTINGS_FTS_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS listings_fts_ai AFTER INSERT ON listings BEGIN "
    "INSERT INTO listings_fts(rowid, title, brand_name_raw) "
    "VALUES (new.id, new.title, new.brand_name_raw); END",
    "CREATE TRIGGER IF NOT EXISTS listings_fts_ad AFTER DELETE ON listings BEGIN "
    "INSERT INTO listings_fts(listings_fts, rowid, title, brand_name_raw) "
    "VALUES ('delete', old.id, old.title, old.brand_name_raw); END",
    "CREATE TRIGGER IF NOT EXISTS listings_fts_au AFTER UPDATE OF title, brand_name_raw "
    "ON listings BEGIN "
    "INSERT INTO listings_fts(listings_fts, rowid, title, brand_name_raw) "
    "VALUES ('delete', old.id, old.title, old.brand_name_raw); "
    "INSERT INTO listings_fts(rowid, title, brand_name_raw) "
    "VALUES (new.id, new.title, new.brand_name_raw); END",
)


def restore_listings_fts_triggers() -> None:
    """A batch table rebuild drops triggers; the FTS index rows keep their ids."""

    for statement in LISTINGS_FTS_TRIGGERS:
        op.execute(statement)


def upgrade() -> None:
    # SQLite DDL is not transactional: a failed earlier attempt leaves its batch copy behind.
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_listings")
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_parser_run_tasks")
    # Older databases do hold T0/T2/T3 rows; keep their provenance as a quality flag.
    op.execute(
        "UPDATE listings SET quality_flags = "
        "json_insert(quality_flags, '$[#]', 'legacy_fetch_tier_' || fetch_tier), "
        "fetch_tier = 'T1' WHERE fetch_tier != 'T1'"
    )
    op.execute("UPDATE parser_run_tasks SET fetch_tier = NULL WHERE fetch_tier != 'T1'")
    # Some databases lack the search index; triggers pointing at it break every later rename.
    fts_missing = not sa.inspect(op.get_bind()).has_table("listings_fts")
    if fts_missing:
        op.execute(
            "CREATE VIRTUAL TABLE listings_fts USING fts5(title, brand_name_raw, "
            "content='listings', content_rowid='id', tokenize='unicode61')"
        )
    with op.batch_alter_table("listings", recreate="always") as batch:
        batch.drop_constraint("ck_listings_fetch_tier", type_="check")
        batch.create_check_constraint("ck_listings_fetch_tier", "fetch_tier = 'T1'")
    restore_listings_fts_triggers()
    with op.batch_alter_table("parser_run_tasks", recreate="always") as batch:
        batch.drop_constraint("ck_tasks_fetch_tier", type_="check")
        batch.create_check_constraint(
            "ck_tasks_fetch_tier", "fetch_tier IS NULL OR fetch_tier = 'T1'"
        )
    if fts_missing:
        op.execute("INSERT INTO listings_fts(listings_fts) VALUES ('rebuild')")


def downgrade() -> None:
    with op.batch_alter_table("parser_run_tasks", recreate="always") as batch:
        batch.drop_constraint("ck_tasks_fetch_tier", type_="check")
        batch.create_check_constraint(
            "ck_tasks_fetch_tier", "fetch_tier IS NULL OR fetch_tier IN ('T1', 'T2', 'T3')"
        )
    with op.batch_alter_table("listings", recreate="always") as batch:
        batch.drop_constraint("ck_listings_fetch_tier", type_="check")
        batch.create_check_constraint(
            "ck_listings_fetch_tier", "fetch_tier IN ('T1', 'T2', 'T3')"
        )
    restore_listings_fts_triggers()
