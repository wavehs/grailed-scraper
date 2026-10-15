"""Re-check stored ``possible_replica`` flags against the current replica rule.

The flag drops a listing from every metric, and fetches rarely revisit sold listings, so a
narrowed rule (0.6: "inspired" only as "inspired by" in the title) needs this one-off pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import CursorResult, String, cast, select, update
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
    """Clear the flag where the rule no longer matches; one commit. Flags are never added.

    A row is only written while its flags are still the ones read: a scrape that changed
    them meanwhile wins, and the next run re-checks that listing.
    """

    flags_text = cast(Listing.quality_flags, String)
    rows = (
        await session.execute(
            select(
                Listing.id,
                Listing.brand_id,
                Listing.title,
                Listing.description,
                Listing.brand_name_raw,
                Listing.quality_flags,
                flags_text.label("flags_text"),
            ).where(flags_text.like(f"%{REPLICA_FLAG}%"))
        )
    ).all()
    flagged = [row for row in rows if REPLICA_FLAG in (row.quality_flags or ())]
    brand_ids: set[int] = set()
    cleared = 0
    for row in flagged:
        if looks_replica(row.title, row.description, row.brand_name_raw):
            continue
        result = await session.execute(
            update(Listing)
            .where(Listing.id == row.id, flags_text == row.flags_text)
            .values(quality_flags=[flag for flag in row.quality_flags if flag != REPLICA_FLAG])
            .execution_options(synchronize_session=False)
        )
        if isinstance(result, CursorResult) and result.rowcount:
            cleared += 1
            if row.brand_id is not None:
                brand_ids.add(row.brand_id)
    if cleared:
        await session.commit()
    return ReflagResult(checked=len(flagged), cleared=cleared, brand_ids=tuple(sorted(brand_ids)))
