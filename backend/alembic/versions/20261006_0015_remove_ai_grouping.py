"""Remove Gemini/Ollama grouping persistence.

Revision ID: 20261006_0015
Revises: 20260824_0014
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261006_0015"
down_revision: str | None = "20260824_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table(
        "listing_model_assignments",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch:
        batch.drop_index("ix_listing_model_assignments_ai_grouping_run")
        batch.drop_constraint("fk_listing_model_assignments_ai_grouping_run_id", type_="foreignkey")
        batch.drop_column("ai_grouping_run_id")
    op.drop_index("ix_ai_grouping_items_batch", table_name="ai_grouping_items")
    op.drop_index("ix_ai_grouping_items_request_key", table_name="ai_grouping_items")
    op.drop_index("ix_ai_grouping_items_run_status", table_name="ai_grouping_items")
    op.drop_table("ai_grouping_items")
    op.drop_index("ix_ai_grouping_batches_run_status", table_name="ai_grouping_batches")
    op.drop_table("ai_grouping_batches")
    op.drop_index("ix_ai_grouping_runs_status_created", table_name="ai_grouping_runs")
    op.drop_table("ai_grouping_runs")


def downgrade() -> None:
    raise RuntimeError("AI grouping persistence was removed; restore from a backup instead")
