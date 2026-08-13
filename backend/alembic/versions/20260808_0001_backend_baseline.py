"""Create the baseline Alembic revision before domain tables are introduced.

Revision ID: 20260808_0001
Revises:
Create Date: 2026-08-08 00:00:00
"""

from collections.abc import Sequence


revision: str = "20260808_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Reserve a migration baseline; domain tables arrive in phase 2."""


def downgrade() -> None:
    """The baseline has no schema objects to remove."""

