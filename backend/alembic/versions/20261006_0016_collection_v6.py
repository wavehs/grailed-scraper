"""Store all designer names of a listing (collaborations).

Revision ID: 20261006_0016
Revises: 20261006_0015
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006_0016"
down_revision: str | None = "20261006_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("listings") as batch:
        batch.add_column(
            sa.Column("designer_names", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("listings") as batch:
        batch.drop_column("designer_names")
