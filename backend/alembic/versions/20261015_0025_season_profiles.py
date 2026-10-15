"""Seasonal profiles of the market, types, brands' types and models (v2 plan 2.5).

Derived data: rebuilt from every sale after each metrics recompute.

Revision ID: 20261015_0025
Revises: 20261015_0024
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0025"
down_revision: str | None = "20261015_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _exists() -> bool:
    return "season_profiles" in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # Tolerant of a table an old startup create_all may have left (see test_domain_migration).
    if _exists():
        return
    op.create_table(
        "season_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_key", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id", ondelete="CASCADE")),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("model_groups.id", ondelete="CASCADE")),
        sa.Column("product_type", sa.String(length=32)),
        sa.Column("department", sa.String(length=32)),
        sa.Column("months", sa.JSON(), nullable=False),
        sa.Column("seasons", sa.JSON(), nullable=False),
        sa.Column("months_covered", sa.Integer(), nullable=False),
        sa.Column("sales_year", sa.Numeric(12, 2), nullable=False),
        sa.Column("correlation", sa.Numeric(6, 4)),
        sa.Column("stable", sa.Boolean()),
        sa.Column("blend_weight", sa.Numeric(6, 4)),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("profile_key", name="uq_season_profiles_key"),
        sa.CheckConstraint(
            "kind IN ('market', 'type', 'brand', 'model')", name="ck_season_profiles_kind"
        ),
    )


def downgrade() -> None:
    if _exists():
        op.drop_table("season_profiles")
