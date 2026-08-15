"""Two-level listing identity and same-seller relist tracking.

Revision ID: 20260815_0009
Revises: 20260814_0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0009"
down_revision: str | None = "20260814_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("listings") as batch:
        batch.add_column(sa.Column("color", sa.String(length=128)))
        batch.add_column(sa.Column("source_product_id", sa.Integer()))
        batch.add_column(sa.Column("source_sku_id", sa.Integer()))
        batch.add_column(sa.Column("source_repost_id", sa.Integer()))
        batch.add_column(sa.Column("cover_asset_key", sa.String(length=64)))
        batch.add_column(sa.Column("cover_content_sha256", sa.String(length=64)))
        batch.add_column(sa.Column("cover_dhash", sa.String(length=16)))
        batch.add_column(sa.Column("identity_version", sa.String(length=32)))
        batch.create_index("ix_listings_source_product", ["source", "source_product_id"])
        batch.create_index("ix_listings_seller_created", ["seller_identity", "created_at"])
        batch.create_index("ix_listings_cover_asset_key", ["cover_asset_key"])
        batch.create_index("ix_listings_cover_content_sha256", ["cover_content_sha256"])
        batch.create_index("ix_listings_cover_dhash", ["cover_dhash"])

    with op.batch_alter_table("model_groups") as batch:
        batch.drop_constraint("ck_model_groups_type", type_="check")
        batch.add_column(sa.Column("merged_into_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_model_groups_merged_into",
            "model_groups",
            ["merged_into_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_model_groups_type",
            "group_type IN ('rule', 'fallback', 'source_product', 'resolved')",
        )

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
            sa.ForeignKey("model_groups.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_listing_model_assignments_group",
        "listing_model_assignments",
        ["model_group_id"],
    )
    op.create_table(
        "physical_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "physical_item_members",
        sa.Column(
            "listing_id",
            sa.Integer(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "physical_item_id",
            sa.Integer(),
            sa.ForeignKey("physical_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_physical_item_members_item", "physical_item_members", ["physical_item_id"]
    )
    op.create_table(
        "identity_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column(
            "left_listing_id",
            sa.Integer(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "right_listing_id",
            sa.Integer(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(length=16)),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("level IN ('model', 'physical')", name="ck_identity_matches_level"),
        sa.CheckConstraint(
            "status IN ('pending', 'auto_confirmed', 'confirmed', 'rejected')",
            name="ck_identity_matches_status",
        ),
        sa.CheckConstraint(
            "relation_type IS NULL OR relation_type = 'relist'",
            name="ck_identity_matches_relation",
        ),
        sa.CheckConstraint(
            "left_listing_id < right_listing_id", name="ck_identity_matches_order"
        ),
        sa.UniqueConstraint(
            "level", "left_listing_id", "right_listing_id", name="uq_identity_matches_pair"
        ),
    )
    op.create_index(
        "ix_identity_matches_queue",
        "identity_matches",
        ["status", "level", "confidence"],
    )
def downgrade() -> None:
    op.drop_index("ix_identity_matches_queue", table_name="identity_matches")
    op.drop_table("identity_matches")
    op.drop_index("ix_physical_item_members_item", table_name="physical_item_members")
    op.drop_table("physical_item_members")
    op.drop_table("physical_items")
    op.drop_index(
        "ix_listing_model_assignments_group", table_name="listing_model_assignments"
    )
    op.drop_table("listing_model_assignments")
    with op.batch_alter_table("model_groups") as batch:
        batch.drop_constraint("ck_model_groups_type", type_="check")
        batch.drop_constraint("fk_model_groups_merged_into", type_="foreignkey")
        batch.drop_column("merged_into_id")
        batch.create_check_constraint(
            "ck_model_groups_type", "group_type IN ('rule', 'fallback')"
        )
    with op.batch_alter_table("listings") as batch:
        batch.drop_index("ix_listings_cover_dhash")
        batch.drop_index("ix_listings_cover_content_sha256")
        batch.drop_index("ix_listings_cover_asset_key")
        batch.drop_index("ix_listings_seller_created")
        batch.drop_index("ix_listings_source_product")
        batch.drop_column("identity_version")
        batch.drop_column("cover_dhash")
        batch.drop_column("cover_content_sha256")
        batch.drop_column("cover_asset_key")
        batch.drop_column("source_repost_id")
        batch.drop_column("source_sku_id")
        batch.drop_column("source_product_id")
        batch.drop_column("color")
