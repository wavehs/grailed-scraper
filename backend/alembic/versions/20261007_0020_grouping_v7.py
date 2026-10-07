"""grouping-v7: retired auto groups, parent overrides and the model blocklist.

``brand_stopwords`` becomes ``model_blocklist``: a "not a model" phrase no longer removes
words from titles, it only forbids the phrase as a model. Existing rows are kept.

Revision ID: 20261007_0020
Revises: 20261006_0019
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_0020"
down_revision: str | None = "20261006_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tolerant of tables an old startup create_all may have left (see test_domain_migration).
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("model_groups")}
    if "retired_at" not in columns:
        with op.batch_alter_table("model_groups") as batch:
            batch.add_column(sa.Column("retired_at", sa.DateTime(timezone=True)))
    if not inspector.has_table("parent_overrides"):
        _create_parent_overrides()
    if not inspector.has_table("model_blocklist"):
        _create_model_blocklist()
    if inspector.has_table("brand_stopwords"):
        op.execute(
            "INSERT OR IGNORE INTO model_blocklist (brand_id, phrase, created_at) "
            "SELECT brand_id, phrase, created_at FROM brand_stopwords"
        )
        op.drop_table("brand_stopwords")


def _create_parent_overrides() -> None:
    op.create_table(
        "parent_overrides",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("model_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "parent_id", sa.Integer(), sa.ForeignKey("model_groups.id", ondelete="CASCADE")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def _create_model_blocklist() -> None:
    op.create_table(
        "model_blocklist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "brand_id",
            sa.Integer(),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phrase", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("brand_id", "phrase", name="uq_model_blocklist_phrase"),
    )


def downgrade() -> None:
    op.create_table(
        "brand_stopwords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "brand_id",
            sa.Integer(),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phrase", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("brand_id", "phrase", name="uq_brand_stopwords_phrase"),
    )
    op.execute(
        "INSERT INTO brand_stopwords (brand_id, phrase, created_at) "
        "SELECT brand_id, phrase, created_at FROM model_blocklist"
    )
    op.drop_table("model_blocklist")
    op.drop_table("parent_overrides")
    with op.batch_alter_table("model_groups") as batch:
        batch.drop_column("retired_at")
