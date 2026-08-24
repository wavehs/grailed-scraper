"""Stage 8 parser runtime state.

Revision ID: 20260808_0004
Revises: 20260808_0003
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0004"
down_revision: str | None = "20260808_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("parser_runs") as batch:
        batch.drop_constraint("ck_parser_runs_status", type_="check")
        batch.add_column(
            sa.Column("phase", sa.String(length=32), nullable=False, server_default="planning")
        )
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_parser_runs_status",
            "status IN ('pending', 'running', 'completed', 'partial', 'failed', "
            "'interrupted', 'cancelled')",
        )


def downgrade() -> None:
    with op.batch_alter_table("parser_runs") as batch:
        batch.drop_constraint("ck_parser_runs_status", type_="check")
        batch.drop_column("heartbeat_at")
        batch.drop_column("phase")
        batch.create_check_constraint(
            "ck_parser_runs_status",
            "status IN ('pending', 'running', 'completed', 'failed', 'interrupted', "
            "'cancelled')",
        )
