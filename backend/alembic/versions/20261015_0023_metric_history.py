"""Daily metric snapshots and monthly sales per scope (v2 plan 1.3 and 1.4).

``group_metrics`` is overwritten by every recompute; ``group_metrics_daily`` keeps a copy
per day for two years. ``group_month_stats`` is derived and rebuilt with the metrics.

Revision ID: 20261015_0023
Revises: 20261015_0022
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0023"
down_revision: str | None = "20261015_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _brand() -> sa.Column[int]:
    return sa.Column(
        "brand_id", sa.Integer(), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    # Tolerant of tables an old startup create_all may have left (see test_domain_migration).
    existing = _tables()
    if "group_metrics_daily" not in existing:
        _create_daily()
    if "group_month_stats" not in existing:
        _create_month_stats()


def _create_daily() -> None:
    op.create_table(
        "group_metrics_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("scope_key", sa.String(length=80), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        _brand(),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("model_groups.id", ondelete="SET NULL")),
        sa.Column("sold_30d", sa.Integer(), nullable=False),
        sa.Column("sold_90d", sa.Integer(), nullable=False),
        sa.Column("active_now", sa.Integer(), nullable=False),
        sa.Column("active_effective", sa.Integer()),
        sa.Column("active_fresh", sa.Integer()),
        sa.Column("median_price_30d", sa.Numeric(14, 2)),
        sa.Column("sell_through_30d", sa.Numeric(7, 6), nullable=False),
        sa.Column("trend_score", sa.Numeric(14, 2)),
        sa.Column("p30", sa.Numeric(7, 6)),
        sa.Column("liquidity_score", sa.Numeric(7, 2)),
        sa.UniqueConstraint("scope_key", "snapshot_date", name="uq_group_metrics_daily_scope_day"),
    )
    op.create_index(
        "ix_group_metrics_daily_brand_day", "group_metrics_daily", ["brand_id", "snapshot_date"]
    )


def _create_month_stats() -> None:
    op.create_table(
        "group_month_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_key", sa.String(length=80), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        _brand(),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("model_groups.id", ondelete="CASCADE")),
        sa.Column("product_type", sa.String(length=32)),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("cond_bucket", sa.String(length=4), nullable=False),
        sa.Column("sold_count", sa.Integer(), nullable=False),
        sa.Column("gmv", sa.Numeric(16, 2), nullable=False),
        sa.Column("p25", sa.Numeric(14, 2)),
        sa.Column("p50", sa.Numeric(14, 2)),
        sa.Column("p75", sa.Numeric(14, 2)),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "scope_key", "month", "cond_bucket", name="uq_group_month_stats_scope_month"
        ),
        sa.CheckConstraint(
            "cond_bucket IN ('all', 'new', 'used')", name="ck_group_month_stats_bucket"
        ),
    )
    op.create_index("ix_group_month_stats_brand", "group_month_stats", ["brand_id"])


def downgrade() -> None:
    existing = _tables()
    for table in ("group_month_stats", "group_metrics_daily"):
        if table in existing:
            op.drop_table(table)
