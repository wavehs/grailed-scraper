"""Cross-brand pass: smoothed P30 and the Liquidity Score (v2 plan 2.2 and 2.4).

Runs once after every brand's raw counts are stored, because both the smoothing parents
(a type across all brands) and the score's reference samples (a section) span brands.
Retired groups keep their frozen metrics and stay out of every reference sample.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from sqlalchemy import Table, bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GroupMetric, GroupMetricDaily, ModelGroup
from app.services.metrics.liquidity import smooth

# A section is scored against its own models once it has 20 with five sales in 90 days;
# it switches at 22 and back at 18 so the score does not flap between recomputes.
REFERENCE_MIN_SALES = 5
REFERENCE_MIN_MODELS = 20
REFERENCE_SWITCH_UP = 22
REFERENCE_SWITCH_DOWN = 18
REFERENCE_QUANTILE = 0.95
SPEED_WEIGHT = 0.6
DEPTH_WEIGHT = 0.4
SECTION = "section"
ALL = "all"
NONE = "none"
_SCORE = Decimal("0.01")
_UPDATE_CHUNK = 500


@dataclass(frozen=True, slots=True)
class RankRow:
    id: int
    scope: str
    brand_id: int
    product_type: str | None
    section: str | None
    p30_sold: int
    p30_cohort: int
    sold_90d: int
    retired: bool = False
    fallback: bool = False
    ls_ref_scope: str | None = None


@dataclass(frozen=True, slots=True)
class Ranked:
    p30: Decimal
    liquidity_score: Decimal | None = None
    ls_ref_scope: str | None = None


def reference_scope(previous: str | None, size: int) -> str:
    """``section`` or ``all`` for a section whose own sample has ``size`` models."""

    if previous == SECTION:
        threshold = REFERENCE_SWITCH_DOWN
    elif previous in (ALL, NONE):
        threshold = REFERENCE_SWITCH_UP
    else:
        threshold = REFERENCE_MIN_MODELS
    return SECTION if size >= threshold else ALL


def liquidity_score(
    p30: Decimal, sold_90d: int, p30_ref: Decimal, sold_ref: Decimal
) -> Decimal | None:
    """``100 · V^0.6 · D^0.4``; None when the reference is empty (no ``ln(1) = 0`` division)."""

    depth_ref = math.log1p(float(sold_ref))
    if p30_ref <= 0 or depth_ref <= 0:
        return None
    speed = min(1.0, float(p30 / p30_ref))
    depth = min(1.0, math.log1p(sold_90d) / depth_ref)
    score = 100 * speed**SPEED_WEIGHT * depth**DEPTH_WEIGHT
    return Decimal(str(score)).quantize(_SCORE, ROUND_HALF_UP)


def rank(rows: Sequence[RankRow]) -> dict[int, Ranked]:
    """Smoothed P30 for every live row; a score for every live model."""

    live = [row for row in rows if not row.retired]
    p30 = _smoothed(live)
    ranked = {row.id: Ranked(p30[row.id]) for row in live}
    models = [row for row in live if row.scope == "model"]
    for row, (score, reference) in _scores(models, p30).items():
        ranked[row] = Ranked(p30[row], score, reference)
    return ranked


def _smoothed(rows: Sequence[RankRow]) -> dict[int, Decimal]:
    brands = [row for row in rows if row.scope == "brand"]
    overall = _rate(brands, Decimal(0))
    types: dict[str, list[RankRow]] = defaultdict(list)
    for row in rows:
        if row.scope == "type" and row.product_type:
            types[row.product_type].append(row)
    across = {name: _rate(items, overall, smoothed=True) for name, items in types.items()}
    in_brand = {
        (row.brand_id, row.product_type): smooth(
            row.p30_sold, row.p30_cohort, across.get(row.product_type or "", overall)
        )
        for row in rows
        if row.scope == "type"
    }
    result: dict[int, Decimal] = {}
    for row in rows:
        if row.scope == "type":
            result[row.id] = in_brand[(row.brand_id, row.product_type)]
            continue
        parent = overall
        if row.scope == "model":
            parent = in_brand.get(
                (row.brand_id, row.product_type), across.get(row.product_type or "", overall)
            )
        result[row.id] = smooth(row.p30_sold, row.p30_cohort, parent)
    return result


def _rate(rows: Iterable[RankRow], parent: Decimal, *, smoothed: bool = False) -> Decimal:
    sold = cohort = 0
    for row in rows:
        sold += row.p30_sold
        cohort += row.p30_cohort
    if smoothed:
        return smooth(sold, cohort, parent)
    return Decimal(sold) / Decimal(cohort) if cohort else parent


def _scores(models: Sequence[RankRow], p30: dict[int, Decimal]) -> dict[int, tuple[Decimal, str]]:
    sample = [row for row in models if not row.fallback and row.sold_90d >= REFERENCE_MIN_SALES]
    references = {ALL: _reference(sample, p30)}
    by_section: dict[str | None, list[RankRow]] = defaultdict(list)
    for row in models:
        by_section[row.section].append(row)
    scores: dict[int, tuple[Decimal, str]] = {}
    for section, rows in by_section.items():
        own = [row for row in sample if row.section == section]
        previous = Counter(row.ls_ref_scope for row in rows if row.ls_ref_scope)
        choice = reference_scope(previous.most_common(1)[0][0] if previous else None, len(own))
        p30_ref, sold_ref = _reference(own, p30) if choice == SECTION else references[ALL]
        for row in rows:
            score = liquidity_score(p30[row.id], row.sold_90d, p30_ref, sold_ref)
            scores[row.id] = (Decimal(0), NONE) if score is None else (score, choice)
    return scores


def _reference(sample: Sequence[RankRow], p30: dict[int, Decimal]) -> tuple[Decimal, Decimal]:
    return (
        _quantile([p30[row.id] for row in sample]),
        _quantile([Decimal(row.sold_90d) for row in sample]),
    )


def _quantile(values: Sequence[Decimal]) -> Decimal:
    """Linear interpolation between closest ranks; 0 for an empty sample."""

    if not values:
        return Decimal(0)
    ordered = sorted(values)
    position = Decimal(str(REFERENCE_QUANTILE)) * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


async def rank_metrics(session: AsyncSession, day: date) -> int:
    """Store smoothed P30 and scores on ``group_metrics`` and on the day's snapshots."""

    statement = select(
        GroupMetric.id,
        GroupMetric.scope_key,
        GroupMetric.scope,
        GroupMetric.brand_id,
        GroupMetric.product_type,
        GroupMetric.section,
        GroupMetric.p30_sold,
        GroupMetric.p30_cohort,
        GroupMetric.sold_90d,
        ModelGroup.retired_at,
        GroupMetric.is_fallback,
        GroupMetric.ls_ref_scope,
    ).outerjoin(ModelGroup, ModelGroup.id == GroupMetric.group_id)
    rows: list[RankRow] = []
    keys: dict[int, str] = {}
    for row in (await session.execute(statement)).tuples().all():
        keys[row[0]] = row[1]
        rows.append(
            RankRow(
                id=row[0],
                scope=row[2],
                brand_id=row[3],
                product_type=row[4],
                section=row[5],
                p30_sold=row[6] or 0,
                p30_cohort=row[7] or 0,
                sold_90d=row[8],
                retired=row[9] is not None,
                fallback=row[10],
                ls_ref_scope=row[11],
            )
        )
    ranked = rank(rows)
    values = [
        {
            "id": row_id,
            "p30": item.p30,
            "liquidity_score": item.liquidity_score,
            "ls_ref_scope": item.ls_ref_scope,
        }
        for row_id, item in ranked.items()
    ]
    for start in range(0, len(values), _UPDATE_CHUNK):
        await session.execute(update(GroupMetric), values[start : start + _UPDATE_CHUNK])
    await _update_snapshots(session, day, {keys[row_id]: item for row_id, item in ranked.items()})
    return len(values)


async def _update_snapshots(session: AsyncSession, day: date, ranked: dict[str, Ranked]) -> None:
    table = cast(Table, GroupMetricDaily.__table__)
    statement = (
        update(table)
        .where(table.c.scope_key == bindparam("key"), table.c.snapshot_date == day)
        .values(p30=bindparam("new_p30"), liquidity_score=bindparam("new_score"))
    )
    values = [
        {"key": key, "new_p30": item.p30, "new_score": item.liquidity_score}
        for key, item in ranked.items()
    ]
    connection = await session.connection()
    for start in range(0, len(values), _UPDATE_CHUNK):
        await connection.execute(statement, values[start : start + _UPDATE_CHUNK])
