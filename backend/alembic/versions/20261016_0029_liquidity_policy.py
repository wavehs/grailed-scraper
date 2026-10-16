"""liquidity-v2: rank by sales, speed, likes and price instead of the growth-based trend.

``trend_score`` rewarded tiny samples (5 sales against 0 is "growth ×6"), so it is dropped.
``windows`` holds the sales of every period the trends screen can rank by; ``period_*``
are the chosen period copied out for sorting. ``liquidity_score`` itself was added by
0024; from here on it holds this policy's score instead of the P30-based one, so that
score's reference sample ``ls_ref_scope`` goes too. The new
columns stay empty until the next metrics recompute (``python -m app.cli metrics``).

Revision ID: 20261016_0029
Revises: 20261015_0028
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261016_0029"
down_revision: str | None = "20261015_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("group_metrics") as batch:
        batch.drop_index("ix_group_metrics_scope_trend")
        batch.drop_column("trend_score")
        batch.drop_column("ls_ref_scope")
        batch.add_column(sa.Column("windows", sa.JSON()))
        batch.add_column(sa.Column("period_sold", sa.Integer()))
        batch.add_column(sa.Column("period_median_price", sa.Numeric(14, 2)))
        batch.add_column(sa.Column("period_median_days", sa.Numeric(10, 2)))
        batch.add_column(sa.Column("period_median_likes", sa.Numeric(10, 1)))
        batch.create_index("ix_group_metrics_scope_liquidity", ["scope", "liquidity_score"])


def downgrade() -> None:
    # Metrics are derived data: the old score comes back empty until a metrics-v1 recompute.
    with op.batch_alter_table("group_metrics") as batch:
        batch.drop_index("ix_group_metrics_scope_liquidity")
        for column in (
            "period_median_likes",
            "period_median_days",
            "period_median_price",
            "period_sold",
            "windows",
        ):
            batch.drop_column(column)
        batch.add_column(sa.Column("ls_ref_scope", sa.String(length=8)))
        batch.add_column(sa.Column("trend_score", sa.Numeric(14, 2)))
        batch.create_index("ix_group_metrics_scope_trend", ["scope", "trend_score"])
