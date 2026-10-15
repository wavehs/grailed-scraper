"""Deals found by the scanner, the person's verdicts on them, and the scanner's state.

Money is in cents like the rest of the API; ``roi`` is a ratio (0.25 = 25% on the money
in). The hit rate is good verdicts over all verdicts, the acceptance number of plan 4.1.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.mutation import MutationRoute
from app.api.settings import get_effective_settings
from app.core.config import Settings
from app.db.models import Brand, DealCandidate, DealScan, Listing, ModelGroup
from app.db.session import get_db
from app.domain.listings import decimal_to_cents
from app.services.deals.scanner import DealScanner

router = APIRouter(prefix="/deals", tags=["deals"], route_class=MutationRoute)
Verdict = Literal["good", "bad"]
VerdictFilter = Literal["all", "unrated", "good", "bad"]


class DealItem(BaseModel):
    id: int
    listing_id: int
    grailed_id: int
    title: str
    url: str
    photo: str | None
    size: str | None
    condition: str | None
    listing_status: str
    seller_rating: Decimal | None
    seller_ratings: int | None
    brand: str | None
    group_id: int
    group: str
    bucket: str
    price: int
    shipping: int | None
    p25: int
    p50: int
    net: int
    roi: Decimal
    liquidity_score: Decimal | None
    found_at: datetime
    updated_at: datetime
    verdict: Verdict | None


class DealSummary(BaseModel):
    total: int
    good: int
    bad: int
    unrated: int
    hit_rate: Decimal | None


class BrandScan(BaseModel):
    brand_id: int
    brand: str
    scanned_to: datetime
    hits: int
    error: str | None


class ScannerState(BaseModel):
    enabled: bool
    interval_minutes: int
    scope: str
    running: bool
    last: dict[str, Any] | None
    last_error: str | None
    skipped_at: str | None
    brands: list[BrandScan]


class DealsResponse(BaseModel):
    items: list[DealItem]
    total: int
    summary: DealSummary
    scanner: ScannerState


class VerdictPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict | None


def _columns() -> Select[Any]:
    return (
        select(DealCandidate, Listing, ModelGroup.name, Brand.name)
        .join(Listing, Listing.id == DealCandidate.listing_id)
        .join(ModelGroup, ModelGroup.id == DealCandidate.group_id)
        .outerjoin(Brand, Brand.id == ModelGroup.brand_id)
    )


def _item(deal: DealCandidate, listing: Listing, group: str, brand: str | None) -> DealItem:
    return DealItem(
        id=deal.id,
        listing_id=listing.id,
        grailed_id=listing.grailed_id,
        title=listing.title,
        url=listing.url,
        photo=listing.cover_photo_url,
        size=listing.size_normalized or listing.size_raw,
        condition=listing.condition,
        listing_status=listing.status,
        seller_rating=listing.seller_rating_avg,
        seller_ratings=listing.seller_rating_count,
        brand=brand,
        group_id=deal.group_id,
        group=group,
        bucket=deal.bucket,
        price=decimal_to_cents(deal.price),
        shipping=decimal_to_cents(deal.shipping) if deal.shipping is not None else None,
        p25=decimal_to_cents(deal.p25),
        p50=decimal_to_cents(deal.p50),
        net=decimal_to_cents(deal.net),
        roi=deal.roi,
        liquidity_score=deal.liquidity_score,
        found_at=deal.found_at,
        updated_at=deal.updated_at,
        verdict=_verdict(deal.verdict),
    )


def _verdict(value: str | None) -> Verdict | None:
    return "good" if value == "good" else "bad" if value == "bad" else None


async def _summary(session: AsyncSession) -> DealSummary:
    counts = dict(
        (
            await session.execute(
                select(DealCandidate.verdict, func.count()).group_by(DealCandidate.verdict)
            )
        )
        .tuples()
        .all()
    )
    good, bad = counts.get("good", 0), counts.get("bad", 0)
    rated = good + bad
    return DealSummary(
        total=sum(counts.values()),
        good=good,
        bad=bad,
        unrated=counts.get(None, 0),
        hit_rate=(Decimal(good) / Decimal(rated)).quantize(Decimal("0.0001")) if rated else None,
    )


async def _scanner(request: Request, session: AsyncSession, settings: Settings) -> ScannerState:
    scanner = getattr(request.app.state, "deal_scanner", None)
    state = scanner.status() if isinstance(scanner, DealScanner) else {}
    rows = (
        await session.execute(
            select(DealScan, Brand.name)
            .join(Brand, Brand.id == DealScan.brand_id)
            .order_by(Brand.name)
        )
    ).tuples()
    return ScannerState(
        enabled=settings.deals_enabled,
        interval_minutes=settings.deals_interval_minutes,
        scope=settings.deals_scope,
        running=bool(state.get("running")),
        last=state.get("last"),
        last_error=state.get("last_error"),
        skipped_at=state.get("skipped_at"),
        brands=[
            BrandScan(
                brand_id=scan.brand_id,
                brand=name,
                scanned_to=scan.scanned_to,
                hits=scan.hits,
                error=scan.error,
            )
            for scan, name in rows
        ],
    )


@router.get("", response_model=DealsResponse)
async def list_deals(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
    verdict: VerdictFilter = "all",
    include_inactive: bool = False,
    sort: Literal["found", "roi"] = "found",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DealsResponse:
    statement = _columns()
    if not include_inactive:
        statement = statement.where(Listing.status == "active")
    if verdict == "unrated":
        statement = statement.where(DealCandidate.verdict.is_(None))
    elif verdict != "all":
        statement = statement.where(DealCandidate.verdict == verdict)
    total = int(await session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    order = (
        (DealCandidate.roi.desc(), DealCandidate.id.desc())
        if sort == "roi"
        else (DealCandidate.found_at.desc(), DealCandidate.id.desc())
    )
    rows = (await session.execute(statement.order_by(*order).limit(limit).offset(offset))).tuples()
    return DealsResponse(
        items=[_item(*row) for row in rows],
        total=total,
        summary=await _summary(session),
        scanner=await _scanner(request, session, settings),
    )


@router.patch("/{deal_id}", response_model=DealItem)
async def rate_deal(
    deal_id: int,
    payload: VerdictPatch,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DealItem:
    deal = await session.get(DealCandidate, deal_id)
    if deal is None:
        raise ApiError(404, "deal_not_found", "The deal does not exist")
    deal.verdict = payload.verdict
    await session.commit()
    row = (await session.execute(_columns().where(DealCandidate.id == deal_id))).tuples().one()
    return _item(*row)


@router.post("/scan", status_code=202)
async def scan_now(request: Request) -> dict[str, bool]:
    scanner = getattr(request.app.state, "deal_scanner", None)
    if not isinstance(scanner, DealScanner):
        raise ApiError(503, "scanner_unavailable", "The deal scanner is not running")
    if not scanner.trigger():
        raise ApiError(409, "market_busy", "A collection or another scan is running")
    return {"started": True}
