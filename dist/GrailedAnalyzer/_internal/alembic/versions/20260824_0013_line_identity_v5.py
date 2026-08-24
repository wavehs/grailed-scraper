"""Add automatic line identity v5 and variant snapshot data.

Revision ID: 20260824_0013
Revises: 20260824_0012
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0013"
down_revision: str | None = "20260824_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scoring_snapshots",
        sa.Column(
            "variant_breakdown",
            sa.JSON(),
            nullable=False,
            server_default='{"colors": [], "sizes": []}',
        ),
    )
    op.drop_index("ix_model_rules_brand_active", table_name="model_rules")
    op.drop_table("model_rules")

    # Manual identity decisions do not carry into the fully automatic v5 rebuild.
    op.execute("DELETE FROM physical_item_members")
    op.execute("DELETE FROM physical_items")
    op.execute("DELETE FROM identity_matches")
    op.execute("UPDATE listings SET size_normalized = 'US 10' WHERE size_normalized = 'US 1E+1'")
    op.execute("UPDATE listings SET identity_version = NULL")
    op.execute("UPDATE listing_model_assignments SET algorithm_version = 'stale'")


def downgrade() -> None:
    op.create_table(
        "model_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("model_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            sa.Integer(),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("include_keywords", sa.JSON(), nullable=False),
        sa.Column("exclude_keywords", sa.JSON(), nullable=False),
        sa.Column("category", sa.String(255)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", name="uq_model_rules_group"),
    )
    op.create_index(
        "ix_model_rules_brand_active", "model_rules", ["brand_id", "is_active"]
    )
    op.drop_column("scoring_snapshots", "variant_breakdown")
