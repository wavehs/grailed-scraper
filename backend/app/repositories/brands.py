"""Persistence for source-brand candidates and manual decisions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Brand, BrandSourceMap, Listing, UnmatchedBrand
from app.domain.listings import slugify


class BrandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_with_counts(self) -> list[tuple[Brand, int]]:
        count = (
            select(Listing.brand_id, func.count(Listing.id).label("listings_count"))
            .group_by(Listing.brand_id)
            .subquery()
        )
        rows = await self._session.execute(
            select(Brand, func.coalesce(count.c.listings_count, 0))
            .outerjoin(count, count.c.brand_id == Brand.id)
            .options(selectinload(Brand.source_mappings))
            .order_by(Brand.name)
        )
        return [(brand, int(total)) for brand, total in rows.all()]

    async def get(self, brand_id: int) -> Brand | None:
        return cast(
            Brand | None,
            await self._session.scalar(
                select(Brand)
                .where(Brand.id == brand_id)
                .options(selectinload(Brand.source_mappings))
            ),
        )

    async def all(self, brand_ids: Sequence[int] | None = None) -> list[Brand]:
        statement = select(Brand).options(selectinload(Brand.source_mappings)).order_by(Brand.id)
        if brand_ids:
            statement = statement.where(Brand.id.in_(brand_ids))
        return list(await self._session.scalars(statement))

    async def upsert_candidate(
        self,
        *,
        brand_id: int,
        source_name: str,
        listings_count: int,
        score: Decimal,
        verified: bool,
        is_subbrand: bool,
        now: datetime,
    ) -> BrandSourceMap:
        mapping = await self._session.scalar(
            select(BrandSourceMap).where(
                BrandSourceMap.brand_id == brand_id,
                BrandSourceMap.source == "grailed",
                BrandSourceMap.source_designer_name == source_name,
            )
        )
        if mapping is None:
            mapping = BrandSourceMap(
                brand_id=brand_id,
                source="grailed",
                source_designer_name=source_name,
                source_slug=slugify(source_name),
                source_designer_id=None,
                listings_count=listings_count,
                match_score=score,
                match_method="auto",
                verified=verified,
                is_subbrand=is_subbrand,
                rejected_at=None,
                updated_at=now,
            )
            self._session.add(mapping)
        elif mapping.rejected_at is None:
            mapping.listings_count = listings_count
            mapping.match_score = score
            mapping.verified = mapping.verified or verified
            mapping.updated_at = now
        await self._session.flush()
        return mapping

    async def decide_mapping(
        self, brand_id: int, mapping_id: int, action: str, now: datetime
    ) -> BrandSourceMap | None:
        mapping = await self._session.scalar(
            select(BrandSourceMap).where(
                BrandSourceMap.id == mapping_id, BrandSourceMap.brand_id == brand_id
            )
        )
        if mapping is None:
            return None
        mapping.verified = action == "confirm"
        mapping.rejected_at = now if action == "reject" else None
        mapping.match_method = "manual"
        mapping.updated_at = now
        await self._session.flush()
        return mapping

    async def record_unmatched(
        self,
        raw_name: str,
        normalized_name: str,
        *,
        suggested_brand_id: int | None,
        best_score: Decimal | None,
        now: datetime | None = None,
    ) -> UnmatchedBrand:
        observed = now or datetime.now(UTC)
        item = await self._session.scalar(
            select(UnmatchedBrand).where(
                UnmatchedBrand.source == "grailed", UnmatchedBrand.raw_name == raw_name
            )
        )
        if item is None:
            item = UnmatchedBrand(
                source="grailed",
                raw_name=raw_name,
                normalized_name=normalized_name,
                occurrences=1,
                suggested_brand_id=suggested_brand_id,
                best_score=best_score,
                first_seen_at=observed,
                last_seen_at=observed,
            )
            self._session.add(item)
        else:
            item.occurrences += 1
            item.suggested_brand_id = suggested_brand_id
            item.best_score = best_score
            item.last_seen_at = observed
        await self._session.flush()
        return item

