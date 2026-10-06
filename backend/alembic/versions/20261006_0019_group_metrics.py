"""Replace per-run scoring snapshots with current group metrics.

Revision ID: 20261006_0019
Revises: 20261006_0018
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0019"
down_revision: str | None = "20261006_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Snapshots were per run, so the dashboard only showed the last run's brands.
    op.execute("DROP TABLE IF EXISTS scoring_snapshots")
    # Derived data; a leftover of the old startup create_all is rebuilt from listings.
    op.execute("DROP TABLE IF EXISTS group_metrics")
    op.create_table(
        "group_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_key", sa.String(length=80), nullable=False, unique=True),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column(
            "brand_id", sa.Integer(), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("product_type", sa.String(length=32)),
        sa.Column("section", sa.String(length=32)),
        sa.Column(
            "group_id", sa.Integer(), sa.ForeignKey("model_groups.id", ondelete="CASCADE")
        ),
        sa.Column("is_line", sa.Boolean(), nullable=False),
        sa.Column("is_fallback", sa.Boolean(), nullable=False),
        sa.Column("listings", sa.Integer(), nullable=False),
        sa.Column("sold_7d", sa.Integer(), nullable=False),
        sa.Column("sold_30d", sa.Integer(), nullable=False),
        sa.Column("sold_prev_30d", sa.Integer(), nullable=False),
        sa.Column("sold_90d", sa.Integer(), nullable=False),
        sa.Column("weekly_sales", sa.JSON(), nullable=False),
        sa.Column("weekly_median_price", sa.JSON(), nullable=False),
        sa.Column("median_price_7d", sa.Numeric(14, 2)),
        sa.Column("median_price_30d", sa.Numeric(14, 2)),
        sa.Column("median_price_90d", sa.Numeric(14, 2)),
        sa.Column("price_change", sa.Numeric(12, 4)),
        sa.Column("median_days_to_sell", sa.Numeric(10, 2)),
        sa.Column("active_now", sa.Integer(), nullable=False),
        sa.Column("new_listings_14d", sa.Integer(), nullable=False),
        sa.Column("sell_through_30d", sa.Numeric(7, 6), nullable=False),
        sa.Column("growth", sa.Numeric(12, 4), nullable=False),
        sa.Column("speed", sa.Numeric(6, 4)),
        sa.Column("trend_score", sa.Numeric(14, 2)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("is_new", sa.Boolean(), nullable=False),
        sa.Column("colors", sa.JSON(), nullable=False),
        sa.Column("sizes", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('model', 'type', 'brand')", name="ck_group_metrics_scope"),
    )
    op.create_index("ix_group_metrics_scope_trend", "group_metrics", ["scope", "trend_score"])
    op.create_index("ix_group_metrics_brand_scope", "group_metrics", ["brand_id", "scope"])


def downgrade() -> None:
    raise RuntimeError("Scoring snapshots were replaced by group metrics; restore a backup")
