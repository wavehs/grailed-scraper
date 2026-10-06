"""Restrict fetch tiers to direct Algolia (T1); browser tiers were removed.

Revision ID: 20261006_0017
Revises: 20261006_0016
"""

from __future__ import annotations

from collections.abc import Sequence

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
    # The browser tiers never produced rows, so no stored value needs rewriting.
    with op.batch_alter_table("listings", recreate="always") as batch:
        batch.drop_constraint("ck_listings_fetch_tier", type_="check")
        batch.create_check_constraint("ck_listings_fetch_tier", "fetch_tier = 'T1'")
    restore_listings_fts_triggers()
    with op.batch_alter_table("parser_run_tasks", recreate="always") as batch:
        batch.drop_constraint("ck_tasks_fetch_tier", type_="check")
        batch.create_check_constraint(
            "ck_tasks_fetch_tier", "fetch_tier IS NULL OR fetch_tier = 'T1'"
        )


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
