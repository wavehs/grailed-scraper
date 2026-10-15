"""Regroup and recompute brand by brand, committing each one.

SQLite has a single writer: one transaction over every brand (Balenciaga alone is 286k
listings) holds the write lock long enough for other writers to hit ``database is locked``.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand
from app.services.grouping import GroupingResult, GroupingService
from app.services.grouping.service import BrandGroupingStats
from app.services.metrics import MetricsResult, MetricsService


async def _selected(session: AsyncSession, brand_ids: Iterable[int] | None) -> list[int]:
    if brand_ids is None:
        return list(await session.scalars(select(Brand.id).order_by(Brand.id)))
    return sorted(set(brand_ids))


async def regroup_brands(
    session: AsyncSession, brand_ids: Iterable[int] | None, *, full: bool = False
) -> GroupingResult:
    """Regroup the brands (all when ``None``) with one commit per brand."""

    started = time.perf_counter()
    stats: list[BrandGroupingStats] = []
    for brand_id in await _selected(session, brand_ids):
        grouped = await GroupingService(session).regroup([brand_id], full=full)
        stats.extend(grouped.brands)
        await session.commit()
    return GroupingResult(stats, time.perf_counter() - started)


async def recompute_brands(
    session: AsyncSession, brand_ids: Iterable[int] | None
) -> MetricsResult:
    """Recompute group metrics of the brands (all when ``None``) with one commit per brand."""

    started = time.perf_counter()
    selected = await _selected(session, brand_ids)
    rows = listings = 0
    for brand_id in selected:
        computed = await MetricsService(session).recompute([brand_id])
        rows += computed.rows
        listings += computed.listings
        await session.commit()
    return MetricsResult(len(selected), rows, listings, time.perf_counter() - started)
