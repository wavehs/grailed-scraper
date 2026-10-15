"""Watchlist of models (v2 plan 3.7).

Revision ID: 20261015_0027
Revises: 20261015_0026
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0027"
down_revision: str | None = "20261015_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _exists() -> bool:
    return "watchlist" in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # Tolerant of a table an old startup create_all may have left (see test_domain_migration).
    if _exists():
        return
    op.create_table(
        "watchlist",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("model_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=500)),
    )


def downgrade() -> None:
    if _exists():
        op.drop_table("watchlist")
