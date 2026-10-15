"""Deal candidates and scanner progress (v2 plan 4.1).

Revision ID: 20261015_0028
Revises: 20261015_0027
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0028"
down_revision: str | None = "20261015_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    # Tolerant of tables an old startup create_all may have left (see test_domain_migration).
    tables = _tables()
    if "deal_candidates" not in tables:
        op.create_table(
            "deal_candidates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "listing_id",
                sa.Integer(),
                sa.ForeignKey("listings.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "group_id",
                sa.Integer(),
                sa.ForeignKey("model_groups.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("bucket", sa.String(length=8), nullable=False),
            sa.Column("price", sa.Numeric(12, 2), nullable=False),
            sa.Column("shipping", sa.Numeric(12, 2)),
            sa.Column("p25", sa.Numeric(12, 2), nullable=False),
            sa.Column("p50", sa.Numeric(12, 2), nullable=False),
            sa.Column("net", sa.Numeric(12, 2), nullable=False),
            sa.Column("roi", sa.Numeric(10, 4), nullable=False),
            sa.Column("liquidity_score", sa.Numeric(6, 2)),
            sa.Column("found_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("verdict", sa.String(length=8)),
            sa.CheckConstraint("bucket IN ('new', 'used')", name="ck_deal_candidates_bucket"),
            sa.CheckConstraint(
                "verdict IS NULL OR verdict IN ('good', 'bad')", name="ck_deal_candidates_verdict"
            ),
        )
        op.create_index("ix_deal_candidates_found", "deal_candidates", ["found_at"])
        op.create_index("ix_deal_candidates_group_id", "deal_candidates", ["group_id"])
    if "deal_scans" not in tables:
        op.create_table(
            "deal_scans",
            sa.Column(
                "brand_id",
                sa.Integer(),
                sa.ForeignKey("brands.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("scanned_to", sa.DateTime(timezone=True), nullable=False),
            sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.String(length=500)),
        )


def downgrade() -> None:
    tables = _tables()
    if "deal_scans" in tables:
        op.drop_table("deal_scans")
    if "deal_candidates" in tables:
        op.drop_index("ix_deal_candidates_group_id", table_name="deal_candidates")
        op.drop_index("ix_deal_candidates_found", table_name="deal_candidates")
        op.drop_table("deal_candidates")
