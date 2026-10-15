"""Liquidity metrics of a scope: effective supply, P30, price stats and Liquidity Score.

Raw counts come from each brand's recompute; P30 smoothing and the Liquidity Score are
filled by the cross-brand pass that follows it (v2 plan 2.1-2.4).

Revision ID: 20261015_0024
Revises: 20261015_0023
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0024"
down_revision: str | None = "20261015_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, Any], ...] = (
    ("active_effective", sa.Integer()),
    ("active_fresh", sa.Integer()),
    ("p30_sold", sa.Integer()),
    ("p30_cohort", sa.Integer()),
    ("p30", sa.Numeric(7, 6)),
    ("gmv_90d", sa.Numeric(16, 2)),
    ("pricing", sa.JSON()),
    ("department", sa.String(length=32)),
    ("liquidity_score", sa.Numeric(7, 2)),
    ("ls_ref_scope", sa.String(length=8)),
)


def _existing() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("group_metrics")}


def upgrade() -> None:
    existing = _existing()
    for name, column_type in _COLUMNS:
        if name not in existing:
            op.add_column("group_metrics", sa.Column(name, column_type))


def downgrade() -> None:
    existing = _existing()
    with op.batch_alter_table("group_metrics") as batch:
        for name, _ in reversed(_COLUMNS):
            if name in existing:
                batch.drop_column(name)
