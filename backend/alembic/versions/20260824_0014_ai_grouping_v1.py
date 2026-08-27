"""Add resumable, budgeted Gemini grouping persistence.

Revision ID: 20260824_0014
Revises: 20260824_0013
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0014"
down_revision: str | None = "20260824_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_grouping_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("base_model", sa.String(length=64), nullable=False),
        sa.Column("review_model", sa.String(length=64), nullable=False),
        sa.Column("grouping_version", sa.String(length=32), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("budget_limit_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 8)),
        sa.Column("actual_cost_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("unique_requests", sa.Integer(), nullable=False),
        sa.Column("completed_items", sa.Integer(), nullable=False),
        sa.Column("ambiguous_items", sa.Integer(), nullable=False),
        sa.Column("failed_items", sa.Integer(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("backup_path", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "mode IN ('canary', 'remaining', 'pending')", name="ck_ai_grouping_runs_mode"
        ),
        sa.CheckConstraint(
            "status IN ('preparing', 'submitted', 'running', 'validating', "
            "'waiting_for_market', 'applying', 'completed', 'failed', 'cancelled', "
            "'interrupted', 'needs_attention', 'rolled_back')",
            name="ck_ai_grouping_runs_status",
        ),
        sa.CheckConstraint("budget_limit_usd >= 0", name="ck_ai_grouping_runs_budget"),
        sa.CheckConstraint("actual_cost_usd >= 0", name="ck_ai_grouping_runs_cost"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_ai_grouping_runs_status_created",
        "ai_grouping_runs",
        ["status", "created_at"],
        if_not_exists=True,
    )

    op.create_table(
        "ai_grouping_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("ai_grouping_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_job_name", sa.String(length=255)),
        sa.Column("provider_display_name", sa.String(length=255)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("failed_requests", sa.Integer(), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('preparing', 'submitted', 'running', 'completed', 'failed', "
            "'cancelled', 'interrupted', 'needs_attention')",
            name="ck_ai_grouping_batches_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_ai_grouping_batches_attempts"),
        sa.CheckConstraint("actual_cost_usd >= 0", name="ck_ai_grouping_batches_cost"),
        sa.UniqueConstraint("provider_job_name", name="uq_ai_grouping_batches_provider_job"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_ai_grouping_batches_run_status",
        "ai_grouping_batches",
        ["run_id", "status"],
        if_not_exists=True,
    )

    op.create_table(
        "ai_grouping_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("ai_grouping_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("ai_grouping_batches.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "listing_id",
            sa.Integer(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("product_type", sa.String(length=64)),
        sa.Column("model_span", sa.String(length=255)),
        sa.Column("normalized_model", sa.String(length=255)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("is_ambiguous", sa.Boolean(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column(
            "target_model_group_id",
            sa.Integer(),
            sa.ForeignKey("model_groups.id", ondelete="SET NULL"),
        ),
        sa.Column("target_stable_key", sa.String(length=512)),
        sa.Column("target_name", sa.String(length=255)),
        sa.Column("target_category", sa.String(length=255)),
        sa.Column("previous_model_group_id", sa.Integer()),
        sa.Column("previous_method", sa.String(length=32)),
        sa.Column("previous_confidence", sa.Numeric(5, 4)),
        sa.Column("previous_algorithm_version", sa.String(length=32)),
        sa.Column("previous_grouping_version", sa.String(length=32)),
        sa.Column("previous_input_hash", sa.String(length=64)),
        sa.Column("previous_ai_grouping_run_id", sa.Integer()),
        sa.Column("previous_updated_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'submitted', 'classified', 'ambiguous', 'failed', "
            "'applied', 'rolled_back')",
            name="ck_ai_grouping_items_status",
        ),
        sa.UniqueConstraint("run_id", "listing_id", name="uq_ai_grouping_items_run_listing"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_ai_grouping_items_run_status",
        "ai_grouping_items",
        ["run_id", "status"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_ai_grouping_items_request_key",
        "ai_grouping_items",
        ["request_key"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_ai_grouping_items_batch",
        "ai_grouping_items",
        ["batch_id"],
        if_not_exists=True,
    )

    with op.batch_alter_table(
        "listing_model_assignments",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch:
        batch.add_column(
            sa.Column(
                "grouping_version",
                sa.String(length=32),
                nullable=False,
                server_default="legacy",
            )
        )
        batch.add_column(sa.Column("input_hash", sa.String(length=64)))
        batch.add_column(sa.Column("ai_grouping_run_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_listing_model_assignments_ai_grouping_run_id",
            "ai_grouping_runs",
            ["ai_grouping_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_listing_model_assignments_grouping_version", ["grouping_version"])
        batch.create_index("ix_listing_model_assignments_input_hash", ["input_hash"])
        batch.create_index("ix_listing_model_assignments_ai_grouping_run", ["ai_grouping_run_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_grouping_items_batch", table_name="ai_grouping_items")
    op.drop_index("ix_ai_grouping_items_request_key", table_name="ai_grouping_items")
    op.drop_index("ix_ai_grouping_items_run_status", table_name="ai_grouping_items")
    op.drop_table("ai_grouping_items")
    op.drop_index("ix_ai_grouping_batches_run_status", table_name="ai_grouping_batches")
    op.drop_table("ai_grouping_batches")

    with op.batch_alter_table(
        "listing_model_assignments",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch:
        batch.drop_index("ix_listing_model_assignments_ai_grouping_run")
        batch.drop_index("ix_listing_model_assignments_input_hash")
        batch.drop_index("ix_listing_model_assignments_grouping_version")
        batch.drop_constraint("fk_listing_model_assignments_ai_grouping_run_id", type_="foreignkey")
        batch.drop_column("ai_grouping_run_id")
        batch.drop_column("input_hash")
        batch.drop_column("grouping_version")

    op.drop_index("ix_ai_grouping_runs_status_created", table_name="ai_grouping_runs")
    op.drop_table("ai_grouping_runs")
