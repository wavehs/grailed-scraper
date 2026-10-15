"""Re-check stored ``possible_replica`` flags against the current replica rule.

The flag drops a listing from every metric, and fetches rarely revisit sold listings, so a
narrowed rule (0.6: "inspired" only as "inspired by" in the title) needs this one-off pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import String, cast, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing
from app.services.normalization.quality import looks_replica

REPLICA_FLAG = "possible_replica"


@dataclass(frozen=True, slots=True)
class ReflagResult:
    checked: int
    cleared: int
    brand_ids: tuple[int, ...]


async def reflag_replicas(session: AsyncSession) -> ReflagResult:
    """Clear the flag where the rule no longer matches; one commit. Flags are never added."""

    rows = (
        await session.execute(
            select(
                Listing.id,
                Listing.brand_id,
                Listing.title,
                Listing.description,
                Listing.brand_name_raw,
                Listing.quality_flags,
            ).where(cast(Listing.quality_flags, String).like(f"%{REPLICA_FLAG}%"))
        )
    ).all()
    flagged = [row for row in rows if REPLICA_FLAG in (row.quality_flags or ())]
    cleared = [
        row
        for row in flagged
        if not looks_replica(row.title, row.description, row.brand_name_raw)
    ]
    if cleared:
        await session.execute(
            update(Listing),
            [
                {
                    "id": row.id,
                    "quality_flags": [flag for flag in row.quality_flags if flag != REPLICA_FLAG],
                }
                for row in cleared
            ],
        )
        await session.commit()
    brand_ids = tuple(sorted({row.brand_id for row in cleared if row.brand_id is not None}))
    return ReflagResult(checked=len(flagged), cleared=len(cleared), brand_ids=brand_ids)
