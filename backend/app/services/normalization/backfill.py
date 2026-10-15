"""Fill the market columns of listings saved before migration 0022, from their ``raw_json``."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import Row, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing
from app.services.normalization.mapping import SourceMappingConfig
from app.services.normalization.market import market_fields

# About 90 commits for the live database; each holds the write lock only briefly.
BACKFILL_BATCH = 5_000


async def backfill_market_fields(
    session: AsyncSession, mapping: SourceMappingConfig, *, batch_size: int = BACKFILL_BATCH
) -> dict[str, object]:
    """Fill every listing whose ``price_initial`` is still empty; re-running resumes."""

    updated = from_raw_json = batches = 0
    last_id = 0
    log = structlog.get_logger(__name__)
    while True:
        rows = (
            await session.execute(
                select(
                    Listing.id,
                    Listing.price,
                    Listing.price_original,
                    Listing.fx_rate,
                    Listing.raw_json,
                )
                .where(Listing.price_initial.is_(None), Listing.id > last_id)
                .order_by(Listing.id)
                .limit(batch_size)
            )
        ).all()
        if not rows:
            break
        await session.execute(update(Listing), [_columns(mapping, row) for row in rows])
        await session.commit()
        batches += 1
        updated += len(rows)
        from_raw_json += sum(bool(row.raw_json) for row in rows)
        last_id = rows[-1].id
        log.info("market_backfill_batch", batch=batches, updated=updated, last_id=last_id)
    return {
        "status": "ok",
        "updated": updated,
        "from_raw_json": from_raw_json,
        "batches": batches,
    }


def _columns(mapping: SourceMappingConfig, row: Row[Any]) -> dict[str, Any]:
    if not row.raw_json:
        # Retention already purged the hit: only the price itself is known.
        return {"id": row.id, "price_initial": row.price}
    foreign = row.price_original is not None
    fields = market_fields(
        mapping,
        row.raw_json,
        price_original=row.price_original if foreign else row.price,
        fx_rate=row.fx_rate if foreign else None,
    )
    return {**fields, "id": row.id, "price_initial": fields["price_initial"] or row.price}
