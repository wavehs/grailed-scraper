"""Review queue and explainable listing-identity history."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.settings import get_effective_settings
from app.core.config import Settings
from app.db.models import (
    IdentityMatch,
    Listing,
    ListingModelAssignment,
    ModelGroup,
    PhysicalItemMember,
)
from app.db.session import get_db
from app.services.identity import IdentityResolver

router = APIRouter(prefix="/identity", tags=["identity"])


class IdentityListingSummary(BaseModel):
    id: int
    grailed_id: int
    title: str
    status: str
    price: int
    brand: str
    category: str | None
    size: str | None
    color: str | None
    cover_photo_url: str | None


class IdentityCandidateResponse(BaseModel):
    id: int
    level: Literal["model", "physical"]
    relation_type: Literal["relist"] | None
    status: Literal["pending", "auto_confirmed", "confirmed", "rejected"]
    confidence: str
    evidence: dict[str, Any]
    left: IdentityListingSummary
    right: IdentityListingSummary


class IdentityCandidateList(BaseModel):
    data: list[IdentityCandidateResponse]
    total: int
    limit: int
    offset: int


class IdentityDecision(BaseModel):
    decision: Literal["confirmed", "rejected"]


class IdentityHistoryResponse(BaseModel):
    listing: IdentityListingSummary
    model_group: dict[str, Any] | None
    physical_item_id: int | None
    members: list[IdentityListingSummary]
    matches: list[dict[str, Any]]


@router.get("/candidates", response_model=IdentityCandidateList)
async def list_candidates(
    session: Annotated[AsyncSession, Depends(get_db)],
    level: Literal["model", "physical"] | None = None,
    status: Literal["pending", "auto_confirmed", "confirmed", "rejected"] = "pending",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IdentityCandidateList:
    filters = [IdentityMatch.status == status]
    if level is not None:
        filters.append(IdentityMatch.level == level)
    total = int(
        await session.scalar(select(func.count(IdentityMatch.id)).where(*filters)) or 0
    )
    matches = list(
        await session.scalars(
            select(IdentityMatch)
            .where(*filters)
            .order_by(IdentityMatch.confidence.desc(), IdentityMatch.id)
            .offset(offset)
            .limit(limit)
        )
    )
    listing_ids = {
        listing_id
        for match in matches
        for listing_id in (match.left_listing_id, match.right_listing_id)
    }
    listings = {
        listing.id: listing
        for listing in await session.scalars(select(Listing).where(Listing.id.in_(listing_ids)))
    }
    return IdentityCandidateList(
        data=[_candidate(match, listings) for match in matches],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/candidates/{match_id}", response_model=IdentityCandidateResponse)
async def decide_candidate(
    match_id: int,
    payload: IdentityDecision,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> IdentityCandidateResponse:
    try:
        match = await IdentityResolver(session, settings).decide(match_id, payload.decision)
    except LookupError as exc:
        raise ApiError(404, "identity_match_not_found", "Identity match does not exist") from exc
    await session.commit()
    listings = {
        listing.id: listing
        for listing in await session.scalars(
            select(Listing).where(
                Listing.id.in_((match.left_listing_id, match.right_listing_id))
            )
        )
    }
    return _candidate(match, listings)


@router.get("/listings/{listing_id}", response_model=IdentityHistoryResponse)
async def listing_identity(
    listing_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> IdentityHistoryResponse:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise ApiError(404, "listing_not_found", "Listing does not exist")
    assignment = await session.get(ListingModelAssignment, listing_id)
    group = (
        await session.get(ModelGroup, assignment.model_group_id)
        if assignment is not None
        else None
    )
    membership = await session.get(PhysicalItemMember, listing_id)
    members: list[Listing] = []
    if membership is not None:
        member_ids = list(
            await session.scalars(
                select(PhysicalItemMember.listing_id).where(
                    PhysicalItemMember.physical_item_id == membership.physical_item_id
                )
            )
        )
        members = list(
            await session.scalars(
                select(Listing).where(Listing.id.in_(member_ids)).order_by(Listing.created_at)
            )
        )
    matches = list(
        await session.scalars(
            select(IdentityMatch)
            .where(
                or_(
                    IdentityMatch.left_listing_id == listing_id,
                    IdentityMatch.right_listing_id == listing_id,
                ),
                IdentityMatch.status.in_(("auto_confirmed", "confirmed")),
            )
            .order_by(IdentityMatch.id)
        )
    )
    return IdentityHistoryResponse(
        listing=_summary(listing),
        model_group=(
            {
                "id": group.id,
                "name": group.name,
                "type": group.group_type,
                "method": assignment.method,
                "confidence": str(assignment.confidence),
            }
            if group is not None and assignment is not None
            else None
        ),
        physical_item_id=membership.physical_item_id if membership is not None else None,
        members=[_summary(item) for item in members],
        matches=[
            {
                "id": match.id,
                "level": match.level,
                "relation_type": match.relation_type,
                "status": match.status,
                "confidence": str(match.confidence),
                "evidence": match.evidence,
            }
            for match in matches
        ],
    )


def _candidate(
    match: IdentityMatch, listings: dict[int, Listing]
) -> IdentityCandidateResponse:
    return IdentityCandidateResponse(
        id=match.id,
        level=match.level,  # type: ignore[arg-type]
        relation_type=match.relation_type,  # type: ignore[arg-type]
        status=match.status,  # type: ignore[arg-type]
        confidence=str(match.confidence),
        evidence=dict(match.evidence),
        left=_summary(listings[match.left_listing_id]),
        right=_summary(listings[match.right_listing_id]),
    )


def _summary(listing: Listing) -> IdentityListingSummary:
    return IdentityListingSummary(
        id=listing.id,
        grailed_id=listing.grailed_id,
        title=listing.title,
        status=listing.status,
        price=_cents(listing.price),
        brand=listing.brand_name_raw,
        category=listing.category,
        size=listing.size_normalized,
        color=listing.color,
        cover_photo_url=listing.cover_photo_url,
    )


def _cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
