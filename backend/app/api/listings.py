"""Listing catalog, single listing and price history."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Integer, false, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.api.cursors import decode_cursor, encode_cursor, require_int
from app.api.errors import ApiError
from app.db.models import Listing, ListingModelAssignment, ListingPriceHistory, ModelGroup
from app.db.session import get_db
from app.domain.listings import decimal_to_cents
from app.services.grouping.service import NONE_SLUG

router = APIRouter(prefix="/listings", tags=["listings"])


class ListingDetail(BaseModel):
    id: int
    grailed_id: int
    url: str
    brand_id: int | None
    title: str
    status: str
    category_path: str | None
    product_type: str | None
    size: str | None
    condition: str | None
    price: int
    sold_price: int | None
    likes_count: int
    days_on_market: int | None
    quality_flags: list[str]
    relist_of_id: int | None
    first_seen_at: datetime
    last_seen_at: datetime


class PriceHistoryRow(BaseModel):
    id: int
    price: int
    observed_at: datetime
    source_run_id: int | None


class PriceHistoryResponse(BaseModel):
    data: list[PriceHistoryRow]


class CatalogRow(BaseModel):
    id: int
    grailed_id: int
    url: str
    title: str
    brand: str
    brand_id: int | None
    product_type: str | None
    status: str
    size: str | None
    color: str | None
    price: int
    created_at: datetime | None
    sold_at: datetime | None
    last_seen_at: datetime
    days_on_market: int | None = None
    model_group_id: int | None
    model_name: str | None
    is_fallback: bool
    model_sold_count: int
    model_active_count: int


class CatalogResponse(BaseModel):
    data: list[CatalogRow]
    limit: int
    next_cursor: str | None


@router.get("", response_model=CatalogResponse)
async def listing_catalog(
    session: Annotated[AsyncSession, Depends(get_db)],
    search: Annotated[str, Query(max_length=200)] = "",
    status: Literal["active", "sold", "removed_pending", "removed"] | None = None,
    product_type: Annotated[str | None, Query(max_length=32)] = None,
    brand_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> CatalogResponse:
    normalized_search = " ".join(search.split()).casefold()
    parameters = {
        "search": normalized_search,
        "status": status,
        "product_type": product_type,
        "brand_id": brand_id,
        "limit": limit,
        "sort": "id_desc",
    }
    if cursor is None:
        snapshot_max_id = int(await session.scalar(select(func.max(Listing.id))) or 0)
        last_id: int | None = None
    else:
        payload = decode_cursor(cursor, "catalog", parameters=parameters)
        if set(payload.context) != {"max_id"} or set(payload.position) != {"id"}:
            raise ApiError(422, "invalid_cursor", "Cursor structure is invalid")
        snapshot_max_id = require_int(payload.context["max_id"], "max_id")
        last_id = require_int(payload.position["id"], "id", minimum=1)

    filters = [Listing.id <= snapshot_max_id]
    statement_parameters: dict[str, str] = {}
    if normalized_search:
        fts_query = _fts_prefix_query(normalized_search)
        if fts_query is None:
            filters.append(false())
        else:
            fts_rows = (
                text("SELECT rowid FROM listings_fts WHERE listings_fts MATCH :fts_query")
                .columns(rowid=Integer)
                .subquery()
            )
            filters.append(Listing.id.in_(select(fts_rows.c.rowid)))
            statement_parameters["fts_query"] = fts_query
    if status:
        filters.append(Listing.status == status)
    if product_type:
        filters.append(Listing.product_type == product_type)
    if brand_id is not None:
        filters.append(Listing.brand_id == brand_id)
    if last_id is not None:
        filters.append(Listing.id < last_id)

    statement = (
        select(Listing, ModelGroup)
        .outerjoin(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
        .outerjoin(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
        .where(*filters)
        .options(
            load_only(
                Listing.id,
                Listing.grailed_id,
                Listing.url,
                Listing.title,
                Listing.brand_name_raw,
                Listing.brand_id,
                Listing.product_type,
                Listing.status,
                Listing.size_normalized,
                Listing.color,
                Listing.price,
                Listing.created_at,
                Listing.first_seen_at,
                Listing.sold_at,
                Listing.last_seen_at,
                Listing.days_on_market,
            )
        )
        .order_by(Listing.id.desc())
        .limit(limit + 1)
    )
    if statement_parameters:
        statement = statement.params(**statement_parameters)
    rows = list((await session.execute(statement)).tuples())
    has_more = len(rows) > limit
    rows = rows[:limit]
    group_ids = {group.id for _, group in rows if group is not None}
    counts: dict[int, dict[str, int]] = {}
    if group_ids:
        for group_id, listing_status, count in await session.execute(
            select(
                ListingModelAssignment.model_group_id,
                Listing.status,
                func.count(Listing.id),
            )
            .join(Listing, Listing.id == ListingModelAssignment.listing_id)
            .where(ListingModelAssignment.model_group_id.in_(group_ids))
            .group_by(ListingModelAssignment.model_group_id, Listing.status)
        ):
            counts.setdefault(group_id, {})[listing_status] = int(count)
    now = datetime.now(UTC)
    return CatalogResponse(
        data=[
            CatalogRow(
                id=item.id,
                grailed_id=item.grailed_id,
                url=item.url,
                title=item.title,
                brand=item.brand_name_raw,
                brand_id=item.brand_id,
                product_type=item.product_type,
                status=item.status,
                size=item.size_normalized,
                color=item.color,
                price=decimal_to_cents(item.price),
                created_at=item.created_at,
                sold_at=item.sold_at,
                last_seen_at=item.last_seen_at,
                days_on_market=_days_on_market(item, now),
                model_group_id=group.id if group else None,
                model_name=group.name if group else None,
                is_fallback=bool(group and group.slug == NONE_SLUG),
                model_sold_count=counts.get(group.id, {}).get("sold", 0) if group else 0,
                model_active_count=counts.get(group.id, {}).get("active", 0) if group else 0,
            )
            for item, group in rows
        ],
        limit=limit,
        next_cursor=(
            encode_cursor(
                "catalog",
                position={"id": rows[-1][0].id},
                context={"max_id": snapshot_max_id},
                parameters=parameters,
            )
            if has_more and rows
            else None
        ),
    )


@router.get("/{listing_id}", response_model=ListingDetail)
async def listing_detail(
    listing_id: int, session: Annotated[AsyncSession, Depends(get_db)]
) -> ListingDetail:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise ApiError(404, "listing_not_found", "Listing does not exist")
    return ListingDetail(
        id=listing.id,
        grailed_id=listing.grailed_id,
        url=listing.url,
        brand_id=listing.brand_id,
        title=listing.title,
        status=listing.status,
        category_path=listing.category_path,
        product_type=listing.product_type,
        size=listing.size_normalized,
        condition=listing.condition,
        price=decimal_to_cents(listing.price),
        sold_price=decimal_to_cents(listing.sold_price) if listing.sold_price is not None else None,
        likes_count=listing.likes_count,
        days_on_market=listing.days_on_market,
        quality_flags=list(listing.quality_flags),
        relist_of_id=listing.relist_of_id,
        first_seen_at=listing.first_seen_at,
        last_seen_at=listing.last_seen_at,
    )


@router.get("/{listing_id}/price-history", response_model=PriceHistoryResponse)
async def listing_price_history(
    listing_id: int, session: Annotated[AsyncSession, Depends(get_db)]
) -> PriceHistoryResponse:
    if await session.get(Listing, listing_id) is None:
        raise ApiError(404, "listing_not_found", "Listing does not exist")
    rows = await session.scalars(
        select(ListingPriceHistory)
        .where(ListingPriceHistory.listing_id == listing_id)
        .order_by(ListingPriceHistory.observed_at)
    )
    return PriceHistoryResponse(
        data=[
            PriceHistoryRow(
                id=row.id,
                price=decimal_to_cents(row.price),
                observed_at=row.observed_at,
                source_run_id=row.source_run_id,
            )
            for row in rows
        ]
    )


def _fts_prefix_query(value: str) -> str | None:
    tokens = re.findall(r"\w+", value, flags=re.UNICODE)
    return " AND ".join(f'"{token}"*' for token in tokens) or None


def _days_on_market(item: Listing, now: datetime) -> int | None:
    created = _aware(item.created_at or item.first_seen_at)
    if item.status == "sold":
        if item.days_on_market is not None:
            return item.days_on_market
        return max((_aware(item.sold_at) - created).days, 0) if item.sold_at else None
    if item.status == "active":
        return max((now - created).days, 0)
    if item.days_on_market is not None:
        return item.days_on_market
    return max((_aware(item.last_seen_at) - created).days, 0)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
