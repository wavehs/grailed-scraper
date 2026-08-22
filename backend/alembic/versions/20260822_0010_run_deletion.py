"""Allow run history deletion without deleting collected listings.

Revision ID: 20260822_0010
Revises: 20260815_0009
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0010"
down_revision: str | None = "20260815_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table(
        "listings",
        naming_convention={
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
        },
    ) as batch:
        batch.drop_constraint(
            "fk_listings_parser_run_id_parser_runs", type_="foreignkey"
        )
        batch.alter_column("parser_run_id", existing_type=sa.Integer(), nullable=True)
        batch.create_foreign_key(
            "fk_listings_parser_run_id",
            "parser_runs",
            ["parser_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("listings") as batch:
        batch.drop_constraint("fk_listings_parser_run_id", type_="foreignkey")
        batch.alter_column("parser_run_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            None,
            "parser_runs",
            ["parser_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )
