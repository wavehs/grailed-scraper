"""liquidity-v2: rank by sales, speed, likes and price instead of the growth-based trend.

``trend_score`` rewarded tiny samples (5 sales against 0 is "growth ×6"), so it is dropped.
The new columns stay empty until the next metrics recompute (``python -m app.cli metrics``).

Revision ID: 20261016_0021
Revises: 20261007_0020
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261016_0021"
down_revision: str | None = "20261007_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("group_metrics") as batch:
        batch.drop_index("ix_group_metrics_scope_trend")
        batch.drop_column("trend_score")
        batch.add_column(sa.Column("median_likes_90d", sa.Numeric(10, 1)))
        batch.add_column(sa.Column("liquidity_score", sa.Numeric(5, 2)))
        batch.create_index("ix_group_metrics_scope_liquidity", ["scope", "liquidity_score"])


def downgrade() -> None:
    # Metrics are derived data: the old score comes back empty until a metrics-v1 recompute.
    with op.batch_alter_table("group_metrics") as batch:
        batch.drop_index("ix_group_metrics_scope_liquidity")
        batch.drop_column("liquidity_score")
        batch.drop_column("median_likes_90d")
        batch.add_column(sa.Column("trend_score", sa.Numeric(14, 2)))
        batch.create_index("ix_group_metrics_scope_trend", ["scope", "trend_score"])
