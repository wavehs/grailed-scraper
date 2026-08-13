"""Stage 11 privacy-safe seller identity and raw-data retention metadata.

Revision ID: 20260813_0007
Revises: 20260808_0006
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0007"
down_revision: str | None = "20260808_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("listings") as batch:
        batch.add_column(sa.Column("seller_identity", sa.Text()))
        batch.add_column(
            sa.Column(
                "seller_identity_mode",
                sa.String(length=16),
                nullable=False,
                server_default="none",
            )
        )
        batch.add_column(sa.Column("raw_json_purged_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE listings SET seller_identity=seller_username_hash, "
        "seller_identity_mode='hashed' WHERE seller_username_hash IS NOT NULL"
    )
    _purge_existing_raw_json()
    with op.batch_alter_table("listings") as batch:
        batch.drop_column("seller_id")
        batch.drop_column("seller_username_hash")
        batch.create_check_constraint(
            "ck_listings_seller_identity_mode",
            "seller_identity_mode IN ('none', 'hashed', 'plain')",
        )


def downgrade() -> None:
    with op.batch_alter_table("listings") as batch:
        batch.drop_constraint("ck_listings_seller_identity_mode", type_="check")
        batch.add_column(sa.Column("seller_username_hash", sa.String(length=64)))
        batch.add_column(sa.Column("seller_id", sa.Integer()))
    op.execute(
        "UPDATE listings SET seller_username_hash=seller_identity "
        "WHERE seller_identity_mode='hashed'"
    )
    with op.batch_alter_table("listings") as batch:
        batch.drop_column("raw_json_purged_at")
        batch.drop_column("seller_identity_mode")
        batch.drop_column("seller_identity")


def _purge_existing_raw_json() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, raw_json FROM listings")).mappings()
    for row in rows:
        raw = row["raw_json"]
        if raw is None:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            payload = {}
        sanitized = _sanitize(payload)
        connection.execute(
            sa.text("UPDATE listings SET raw_json=:raw_json WHERE id=:listing_id"),
            {"raw_json": json.dumps(sanitized), "listing_id": row["id"]},
        )


def _sanitize(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            normalized = str(key).casefold()
            if _sensitive_key(normalized, path):
                continue
            if normalized == "location":
                if isinstance(nested, str) and len(nested.strip()) == 2:
                    result[str(key)] = nested.strip().upper()
                continue
            result[str(key)] = _sanitize(nested, (*path, normalized))
        return result
    if isinstance(value, list):
        return [_sanitize(item, path) for item in value]
    return value


def _sensitive_key(key: str, path: tuple[str, ...]) -> bool:
    return key in {
        "username",
        "seller_username",
        "seller_id",
        "email",
        "latitude",
        "longitude",
        "coordinates",
        "address",
        "city",
        "state",
        "postal_code",
        "postcode",
        "zip",
        "zipcode",
    } or (key == "id" and "seller" in path)
