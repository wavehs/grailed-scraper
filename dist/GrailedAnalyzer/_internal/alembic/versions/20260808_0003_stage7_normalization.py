"""Add stage-seven brand normalization tables.

Revision ID: 20260808_0003
Revises: 20260808_0002
Create Date: 2026-08-08 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0003"
down_revision: str | None = "20260808_0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "brands",
        sa.Column("aliases", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "brands",
        sa.Column(
            "include_subbrands",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_table(
        "brand_source_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "brand_id",
            sa.Integer(),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_designer_name", sa.String(length=255), nullable=False),
        sa.Column("source_slug", sa.String(length=255)),
        sa.Column("source_designer_id", sa.String(length=128)),
        sa.Column("listings_count", sa.Integer(), nullable=False),
        sa.Column("match_score", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column("match_method", sa.String(length=32), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("is_subbrand", sa.Boolean(), nullable=False),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "brand_id",
            "source",
            "source_designer_name",
            name="uq_brand_source_map_identity",
        ),
        sa.CheckConstraint(
            "NOT (verified = 1 AND rejected_at IS NOT NULL)",
            name="ck_brand_source_map_state",
        ),
    )
    op.create_index(
        "ix_brand_source_map_brand_verified",
        "brand_source_map",
        ["brand_id", "verified"],
    )
    op.create_table(
        "unmatched_brands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("raw_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column(
            "suggested_brand_id",
            sa.Integer(),
            sa.ForeignKey("brands.id", ondelete="SET NULL"),
        ),
        sa.Column("best_score", sa.Numeric(precision=6, scale=5)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "raw_name", name="uq_unmatched_brands_source_name"),
    )
    op.create_index(
        "ix_unmatched_brands_source_normalized",
        "unmatched_brands",
        ["source", "normalized_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_unmatched_brands_source_normalized", table_name="unmatched_brands"
    )
    op.drop_table("unmatched_brands")
    op.drop_index("ix_brand_source_map_brand_verified", table_name="brand_source_map")
    op.drop_table("brand_source_map")
    with op.batch_alter_table("brands") as batch:
        batch.drop_column("include_subbrands")
        batch.drop_column("aliases")
