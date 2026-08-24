"""Add market-v4 demand, evidence status, and raw sold likes.

Revision ID: 20260822_0011
Revises: 20260822_0010
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0011"
down_revision: str | None = "20260822_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scoring_snapshots") as batch:
        batch.add_column(
            sa.Column("exact_sold_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("median_sold_likes", sa.Numeric(14, 2)))
        batch.add_column(sa.Column("demand_score", sa.Numeric(6, 2)))
        batch.add_column(
            sa.Column("scoring_status", sa.String(32), nullable=False, server_default="scored")
        )
        batch.alter_column(
            "liquidity_score", existing_type=sa.Numeric(6, 2), nullable=True
        )
        batch.alter_column(
            "market_opportunity_score", existing_type=sa.Numeric(6, 2), nullable=True
        )
        batch.create_index(
            "ix_scoring_snapshots_brand_window_demand",
            ["brand_id", "window_days", "demand_score"],
        )


def downgrade() -> None:
    op.execute(
        "UPDATE scoring_snapshots SET liquidity_score = 0 WHERE liquidity_score IS NULL"
    )
    op.execute(
        "UPDATE scoring_snapshots SET market_opportunity_score = 0 "
        "WHERE market_opportunity_score IS NULL"
    )
    with op.batch_alter_table("scoring_snapshots") as batch:
        batch.drop_index("ix_scoring_snapshots_brand_window_demand")
        batch.alter_column(
            "market_opportunity_score", existing_type=sa.Numeric(6, 2), nullable=False
        )
        batch.alter_column(
            "liquidity_score", existing_type=sa.Numeric(6, 2), nullable=False
        )
        batch.drop_column("scoring_status")
        batch.drop_column("demand_score")
        batch.drop_column("median_sold_likes")
        batch.drop_column("exact_sold_count")
