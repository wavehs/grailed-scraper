"""Stage 9 versioned scoring and analytics segments.

Revision ID: 20260808_0005
Revises: 20260808_0004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0005"
down_revision: str | None = "20260808_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stable_key", sa.String(length=512), nullable=False, unique=True),
        sa.Column(
            "brand_id",
            sa.Integer(),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=255)),
        sa.Column("group_type", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("group_type IN ('rule', 'fallback')", name="ck_model_groups_type"),
    )
    op.create_index(
        "ix_model_groups_brand_type", "model_groups", ["brand_id", "group_type"]
    )
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
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("include_keywords", sa.JSON(), nullable=False),
        sa.Column("exclude_keywords", sa.JSON(), nullable=False),
        sa.Column("category", sa.String(length=255)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", name="uq_model_rules_group"),
    )
    op.create_index(
        "ix_model_rules_brand_active", "model_rules", ["brand_id", "is_active"]
    )
    op.create_table(
        "scoring_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "parser_run_id",
            sa.Integer(),
            sa.ForeignKey("parser_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "model_group_id",
            sa.Integer(),
            sa.ForeignKey("model_groups.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            sa.Integer(),
            sa.ForeignKey("brands.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False),
        sa.Column("sold_count", sa.Integer(), nullable=False),
        sa.Column("median_sold_price", sa.Numeric(14, 2)),
        sa.Column("median_days_to_sell", sa.Numeric(10, 2)),
        sa.Column("median_sold_likes_per_day", sa.Numeric(14, 4)),
        sa.Column("sell_through", sa.Numeric(7, 6), nullable=False),
        sa.Column("liquidity_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("price_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("confidence_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("market_opportunity_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("component_breakdown", sa.JSON(), nullable=False),
        sa.Column("confidence_factors", sa.JSON(), nullable=False),
        sa.Column("quality_summary", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "parser_run_id",
            "model_group_id",
            "model_version",
            "window_days",
            name="uq_scoring_snapshots_identity",
        ),
        sa.CheckConstraint("window_days IN (30, 90)", name="ck_scoring_snapshots_window"),
    )
    op.create_index(
        "ix_scoring_snapshots_group_window_run",
        "scoring_snapshots",
        ["model_group_id", "window_days", "parser_run_id"],
    )
    op.create_index(
        "ix_scoring_snapshots_brand_window_opportunity",
        "scoring_snapshots",
        ["brand_id", "window_days", "market_opportunity_score"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scoring_snapshots_brand_window_opportunity",
        table_name="scoring_snapshots",
    )
    op.drop_index(
        "ix_scoring_snapshots_group_window_run", table_name="scoring_snapshots"
    )
    op.drop_table("scoring_snapshots")
    op.drop_index("ix_model_rules_brand_active", table_name="model_rules")
    op.drop_table("model_rules")
    op.drop_index("ix_model_groups_brand_type", table_name="model_groups")
    op.drop_table("model_groups")
