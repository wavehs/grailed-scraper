"""Seed the canonical live brand scope.

Revision ID: 20260814_0008
Revises: 20260813_0007
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0008"
down_revision: str | None = "20260813_0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

BRANDS = (
    ("Chrome Hearts", "chrome-hearts", ["CH"]),
    ("Enfants Riches Déprimés", "enfants-riches-deprimes", ["ERD"]),
    ("Rick Owens", "rick-owens", ["RO", "DRKSHDW"]),
    ("Raf Simons", "raf-simons", []),
    ("Undercover", "undercover", ["UC"]),
    ("Number (N)ine", "number-nine", ["Number Nine", "N(N)"]),
    ("Vetements", "vetements", ["VTMNTS"]),
    ("Balenciaga", "balenciaga", ["Bala"]),
    ("Vivienne Westwood", "vivienne-westwood", ["VW"]),
    ("Yohji Yamamoto", "yohji-yamamoto", ["Yohji", "Y's"]),
    ("Comme des Garçons", "comme-des-garcons", ["CDG"]),
    ("Stone Island", "stone-island", ["SI", "SISP"]),
    ("Arc'teryx", "arcteryx", ["Arcteryx"]),
    ("Arc'teryx Veilance", "arcteryx-veilance", ["Veilance"]),
    ("Kapital", "kapital", []),
    ("Visvim", "visvim", []),
    ("Carol Christian Poell", "carol-christian-poell", ["CCP"]),
    ("Maison Margiela", "maison-margiela", ["Margiela", "MMM"]),
    ("Bape", "bape", ["BAPE", "AAPE"]),
    ("Hysteric Glamour", "hysteric-glamour", ["HG"]),
    ("Jean Paul Gaultier", "jean-paul-gaultier", ["JPG", "Gaultier"]),
)


def upgrade() -> None:
    brands = sa.table(
        "brands",
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("aliases", sa.JSON),
        sa.column("include_subbrands", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    existing = set(bind.execute(sa.select(brands.c.name)).scalars())
    now = datetime.now(UTC)
    rows = [
        {
            "name": name,
            "slug": slug,
            "aliases": aliases,
            "include_subbrands": False,
            "created_at": now,
            "updated_at": now,
        }
        for name, slug, aliases in BRANDS
        if name not in existing
    ]
    if rows:
        bind.execute(brands.insert(), rows)


def downgrade() -> None:
    # Seed rows may already own listings; deleting them would destroy user data.
    pass
