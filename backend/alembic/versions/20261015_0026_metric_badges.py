"""Model badges (❔ 🔥 💎 🐢 ⚠️) from the cross-brand pass (v2 plan 3.5).

Revision ID: 20261015_0026
Revises: 20261015_0025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0026"
down_revision: str | None = "20261015_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("group_metrics")}


def upgrade() -> None:
    if "badges" not in _existing():
        op.add_column("group_metrics", sa.Column("badges", sa.JSON()))


def downgrade() -> None:
    if "badges" in _existing():
        with op.batch_alter_table("group_metrics") as batch:
            batch.drop_column("badges")
