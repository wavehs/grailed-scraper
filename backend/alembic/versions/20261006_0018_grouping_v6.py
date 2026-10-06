"""grouping-v6: brand + product type + model groups, manual rules and simple relists.

The old identity tables (model/physical matches, image fingerprints) are removed. Model
groups and assignments are derived data and are rebuilt by the next regroup.

Revision ID: 20261006_0018
Revises: 20261006_0017
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0018"
down_revision: str | None = "20261006_0017"
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


def upgrade() -> None:
    # Snapshots reference the old groups; they are recalculated from listings.
    op.execute("DELETE FROM scoring_snapshots")
    # A listing_overrides leftover of the old startup create_all points at the dropped groups.
    for table in (
        "listing_overrides",
        "identity_matches",
        "physical_item_members",
        "physical_items",
        "listing_model_assignments",
        "model_groups",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")

    op.create_table(
        "model_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "brand_id", sa.Integer(), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("product_type", sa.String(length=32), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("model_groups.id", name="fk_model_groups_parent", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("infer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("support", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("brand_id", "product_type", "slug", name="uq_model_groups_identity"),
        sa.CheckConstraint(
            "status IN ('confirmed', 'auto', 'ignored')", name="ck_model_groups_status"
        ),
        sa.CheckConstraint(
            "source IN ('seed', 'mined', 'user', 'system')", name="ck_model_groups_source"
        ),
    )
    op.create_index("ix_model_groups_brand_type", "model_groups", ["brand_id", "product_type"])
    op.create_index("ix_model_groups_parent", "model_groups", ["parent_id"])
    op.create_table(
        "listing_model_assignments",
        sa.Column(
            "listing_id",
            sa.Integer(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "model_group_id",
            sa.Integer(),
            sa.ForeignKey("model_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_listing_model_assignments_group", "listing_model_assignments", ["model_group_id"]
    )
    op.create_table(
        "listing_overrides",
        sa.Column(
            "listing_id",
            sa.Integer(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "model_group_id",
            sa.Integer(),
            sa.ForeignKey("model_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # A create_all leftover has the same shape and may already hold user stopwords.
    if not sa.inspect(op.get_bind()).has_table("brand_stopwords"):
        _create_brand_stopwords()

    with op.batch_alter_table("brands") as batch:
        batch.add_column(sa.Column("grouping_hash", sa.String(length=64)))
        batch.add_column(sa.Column("grouped_at", sa.DateTime(timezone=True)))

    for index in (
        "ix_listings_cover_asset_key",
        "ix_listings_cover_content_sha256",
        "ix_listings_cover_dhash",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index}")
    with op.batch_alter_table("listings", recreate="always") as batch:
        batch.drop_column("cover_asset_key")
        batch.drop_column("cover_content_sha256")
        batch.drop_column("cover_dhash")
        batch.drop_column("identity_version")
        batch.add_column(sa.Column("category_path", sa.String(length=255)))
        batch.add_column(sa.Column("product_type", sa.String(length=32)))
        batch.add_column(sa.Column("relist_of_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_listings_relist_of", "listings", ["relist_of_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index(
            "ix_listings_brand_type_status", ["brand_id", "product_type", "status"]
        )
    for statement in LISTINGS_FTS_TRIGGERS:
        op.execute(statement)
    op.execute(
        "UPDATE listings SET category_path = json_extract(raw_json, '$.category_path') "
        "WHERE category_path IS NULL AND json_type(raw_json, '$.category_path') = 'text'"
    )


def _create_brand_stopwords() -> None:
    op.create_table(
        "brand_stopwords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "brand_id", sa.Integer(), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("phrase", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("brand_id", "phrase", name="uq_brand_stopwords_phrase"),
    )


def downgrade() -> None:
    raise RuntimeError("grouping-v6 replaced the identity tables; restore from a backup instead")
