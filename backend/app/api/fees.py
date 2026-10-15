"""The Grailed fee schedule for the reseller calculator of the model card (v2 plan 3.4)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.mutation import MutationRoute
from app.services.metrics.fees import FeeSchedule, load_fees

router = APIRouter(prefix="/fees", tags=["fees"], route_class=MutationRoute)


@router.get("", response_model=FeeSchedule)
async def fee_schedule() -> FeeSchedule:
    """``config/fees.yaml`` as validated at startup; amounts and rates are decimal strings."""

    return load_fees()
