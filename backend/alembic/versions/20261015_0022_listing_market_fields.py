"""Keep market fields that used to live only in ``raw_json``.

Retention purges ``raw_json`` 90 days after a listing was last seen; the price path,
shipping, offer flags and the seller's score would be lost with it. Existing rows are
filled by ``python -m app.cli backfill-market-fields``.

Revision ID: 20261015_0022
Revises: 20261015_0021
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0022"
down_revision: str | None = "20261015_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, Any], ...] = (
    ("price_initial", sa.Numeric(14, 2)),
    ("price_drop_count", sa.Integer()),
    ("price_updated_at", sa.DateTime(timezone=True)),
    ("bumped_at", sa.DateTime(timezone=True)),
    ("sold_shipping_price", sa.Numeric(14, 2)),
    ("ship_domestic", sa.Numeric(14, 2)),
    ("makeoffer", sa.Boolean()),
    ("buynow", sa.Boolean()),
    ("department", sa.String(length=32)),
    ("seller_rating_avg", sa.Numeric(4, 3)),
    ("seller_rating_count", sa.Integer()),
    ("seller_txn_count", sa.Integer()),
    ("seller_trusted", sa.Boolean()),
)


def _existing() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("listings")}


def upgrade() -> None:
    # Tolerant of columns an old startup create_all may have left (see test_domain_migration).
    existing = _existing()
    # Plain ADD COLUMN: a batch rebuild would copy every listing of a large database.
    for name, column_type in _COLUMNS:
        if name not in existing:
            op.add_column("listings", sa.Column(name, column_type))


def downgrade() -> None:
    existing = _existing()
    with op.batch_alter_table("listings") as batch:
        for name, _ in reversed(_COLUMNS):
            if name in existing:
                batch.drop_column(name)
