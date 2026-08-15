"""Watermark and active-listing lifecycle persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing, ParserWatermark


class LifecycleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def watermark(
        self, source: str, brand_id: int, index_type: str
    ) -> ParserWatermark | None:
        return cast(
            ParserWatermark | None,
            await self._session.scalar(
                select(ParserWatermark).where(
                    ParserWatermark.source == source,
                    ParserWatermark.brand_id == brand_id,
                    ParserWatermark.index_type == index_type,
                )
            ),
        )

    async def advance_watermark(
        self,
        *,
        source: str,
        brand_id: int,
        index_type: str,
        last_key_value: str,
        mode: str,
        now: datetime,
    ) -> ParserWatermark:
        item = await self.watermark(source, brand_id, index_type)
        if item is None:
            item = ParserWatermark(
                source=source,
                brand_id=brand_id,
                index_type=index_type,
                last_key_value=last_key_value,
                last_run_at=now,
                full_refresh_at=now if mode == "full" else None,
            )
            self._session.add(item)
        else:
            item.last_key_value = last_key_value
            item.last_run_at = now
            if mode == "full":
                item.full_refresh_at = now
        await self._session.flush()
        return item

    async def refresh_candidates(
        self, brand_id: int | None = None, *, limit: int | None = None
    ) -> list[Listing]:
        statement = select(Listing).where(Listing.status.in_(("active", "removed_pending")))
        if brand_id is not None:
            statement = statement.where(Listing.brand_id == brand_id)
        if limit is not None:
            statement = statement.limit(limit)
        return list(await self._session.scalars(statement.order_by(Listing.grailed_id)))

    async def apply_missing(
        self,
        listings: Sequence[Listing],
        *,
        now: datetime | None = None,
        confirm_after: timedelta = timedelta(hours=48),
    ) -> tuple[int, int]:
        observed = now or datetime.now(UTC)
        pending = removed = 0
        for listing in listings:
            checked_at = _aware(listing.removed_checked_at)
            if listing.status == "active":
                listing.status = "removed_pending"
                listing.removed_checked_at = observed
                pending += 1
            elif (
                listing.status == "removed_pending"
                and checked_at is not None
                and observed - checked_at >= confirm_after
            ):
                listing.status = "removed"
                removed += 1
        await self._session.flush()
        return pending, removed


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
