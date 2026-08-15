"""Idempotent SQLite persistence for normalized listings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.db.models import Listing, ListingPriceHistory
from app.domain.listings import ListingData


@dataclass(frozen=True)
class UpsertResult:
    inserted: int
    updated: int
    price_changes: int


class ListingRepository:
    """Write listings in small transactional batches without losing source data."""

    batch_size = 200

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_batch(self, listings: Sequence[ListingData]) -> UpsertResult:
        """Upsert a sequence by ``grailed_id`` and record only real price changes.

        The caller owns the surrounding transaction.  Duplicate ids in one input
        are deliberately collapsed to the final payload before an SQL statement is
        generated, which also prevents duplicate price-history rows.
        """

        deduplicated = {listing.grailed_id: listing for listing in listings}
        records = list(deduplicated.values())
        inserted = updated = price_changes = 0

        for start in range(0, len(records), self.batch_size):
            batch = records[start : start + self.batch_size]
            existing_prices = await self._existing_prices([item.grailed_id for item in batch])
            inserted += sum(item.grailed_id not in existing_prices for item in batch)
            updated += sum(item.grailed_id in existing_prices for item in batch)
            changed = [
                item
                for item in batch
                if (old_price := existing_prices.get(item.grailed_id)) is not None
                and old_price != item.price
            ]

            await self._upsert_records(batch)
            listing_ids = await self._listing_ids([item.grailed_id for item in changed])
            self._session.add_all(
                ListingPriceHistory(
                    listing_id=listing_ids[item.grailed_id],
                    price=item.price,
                    observed_at=item.last_seen_at,
                    source_run_id=item.parser_run_id,
                )
                for item in changed
            )
            price_changes += len(changed)

        await self._session.flush()
        return UpsertResult(inserted=inserted, updated=updated, price_changes=price_changes)

    async def _existing_prices(self, grailed_ids: Sequence[int]) -> dict[int, Decimal]:
        result = await self._session.execute(
            select(Listing.grailed_id, Listing.price).where(Listing.grailed_id.in_(grailed_ids))
        )
        return dict(result.tuples().all())

    async def _listing_ids(self, grailed_ids: Sequence[int]) -> dict[int, int]:
        if not grailed_ids:
            return {}
        result = await self._session.execute(
            select(Listing.grailed_id, Listing.id).where(Listing.grailed_id.in_(grailed_ids))
        )
        return dict(result.tuples().all())

    async def _upsert_records(self, records: Sequence[ListingData]) -> None:
        values = [self._values(record) for record in records]
        statement = insert(Listing).values(values)
        update_columns = {
            name: self._coalesced(statement.excluded[name], getattr(Listing, name))
            for name in self._mutable_columns()
        }
        # A reappeared/sold listing must clear its previous removal probe marker.
        update_columns["removed_checked_at"] = statement.excluded.removed_checked_at
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[Listing.grailed_id], set_=update_columns
            )
        )

    @staticmethod
    def _coalesced(
        incoming: ColumnElement[object], existing: ColumnElement[object]
    ) -> ColumnElement[object]:
        return func.coalesce(incoming, existing)

    @staticmethod
    def _mutable_columns() -> tuple[str, ...]:
        return (
            "source",
            "status",
            "url",
            "title",
            "description",
            "brand_name_raw",
            "brand_slug",
            "brand_id",
            "category",
            "subcategory",
            "size_raw",
            "size_normalized",
            "condition_raw",
            "condition",
            "color",
            "source_product_id",
            "source_sku_id",
            "source_repost_id",
            "price",
            "price_original",
            "currency_original",
            "fx_rate",
            "sold_price",
            "likes_count",
            "created_at",
            "sold_at",
            "sold_at_is_estimated",
            "updated_at",
            "last_seen_at",
            "removed_checked_at",
            "days_on_market",
            "cover_photo_url",
            "cover_asset_key",
            "cover_content_sha256",
            "cover_dhash",
            "photo_urls",
            "photo_count",
            "seller_identity",
            "seller_identity_mode",
            "seller_country",
            "quality_flags",
            "fetch_tier",
            "parser_run_id",
            "raw_json",
            "raw_json_purged_at",
            "schema_version",
            "identity_version",
        )

    @staticmethod
    def _values(listing: ListingData) -> dict[str, object]:
        return listing.model_dump(mode="python")
