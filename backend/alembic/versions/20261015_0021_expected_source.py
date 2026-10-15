"""Record whether a task's expected hit count was exact or an estimate.

Algolia reports ``exhaustiveNbHits=false`` for large result sets; coverage against such a
count is shown as an estimate instead of being reconciled to 100%.

Revision ID: 20261015_0021
Revises: 20261007_0020
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0021"
down_revision: str | None = "20261007_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tolerant of a column an old startup create_all may have left (see test_domain_migration).
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("parser_run_tasks")
    }
    if "expected_source" not in columns:
        with op.batch_alter_table("parser_run_tasks") as batch:
            batch.add_column(sa.Column("expected_source", sa.String(length=16)))


def downgrade() -> None:
    with op.batch_alter_table("parser_run_tasks") as batch:
        batch.drop_column("expected_source")
