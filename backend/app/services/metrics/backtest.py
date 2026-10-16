"""Backtest of the liquidity metrics: the state at ``T`` against what happened after (plan 2.7).

Every scope is rebuilt as it stood at ``T = now - as_of_days`` (``MetricsService.scopes``
with ``historical``), scored with the same calculator, P30 smoothing and saved liquidity
policy as a recompute, and compared with two targets:

- volume: the group's sales in ``(T, now]`` — for depth ``D`` and the Liquidity Score
  against the baseline ``sold_30d``;
- velocity: the share of the supply at ``T`` sold in ``(T, T+30]`` — for smoothed P30 and
  the Liquidity Score against the baseline ``sell_through_30d``. A rate is never scored
  against a count.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand
from app.services.grouping.descriptors import DESCRIPTOR_PREFIX
from app.services.grouping.policy import REVIEW_TYPE, load_policy
from app.services.metrics.calculator import MetricListing, ScopeMetrics, compute_metrics
from app.services.metrics.liquidity import LiquidityCandidate, LiquidityPolicy, liquidity_scores
from app.services.metrics.liquidity_store import effective_policy
from app.services.metrics.ranking import RankRow, rank
from app.services.metrics.service import (
    METRICS_VERSION,
    BrandScopes,
    MetricsService,
    liquidity_candidate,
)

AS_OF_DAYS = 30
FORWARD_DAYS = 30
MIN_LISTINGS = 5
# A new metric passes when its ρ is at most this far below the best baseline's.
GATE_TOLERANCE = 0.02
NEW = "new"
BASELINE = "baseline"
INFO = "info"
VOLUME = "volume"
VELOCITY = "velocity"
_TOP_GROUPS = 15


@dataclass(frozen=True, slots=True)
class Outcome:
    """One evaluated model group: its metrics at ``T`` and what happened after."""

    key: str
    brand_id: int
    name: str
    listings_at: int
    supply_at: int
    sold_after: int
    # None when fewer than MIN_LISTINGS were on sale at T.
    forward_sell_through: float | None
    sold_30d: int
    sold_90d: int
    sell_through_30d: Decimal
    p30_raw: Decimal | None
    p30: Decimal | None
    liquidity_score: Decimal | None


@dataclass(frozen=True, slots=True)
class Correlation:
    metric: str
    role: str
    rho: float | None
    groups: int


@dataclass(frozen=True, slots=True)
class Verdict:
    target: str
    metric: str
    rho: float | None
    baseline: str | None
    baseline_rho: float | None
    passed: bool | None


@dataclass(frozen=True, slots=True)
class BacktestReport:
    as_of: datetime
    now: datetime
    horizon: datetime
    brands: int
    outcomes: tuple[Outcome, ...]
    volume: tuple[Correlation, ...]
    velocity: tuple[Correlation, ...]

    @property
    def verdicts(self) -> tuple[Verdict, ...]:
        return (*_verdicts(VOLUME, self.volume), *_verdicts(VELOCITY, self.velocity))


@dataclass(frozen=True, slots=True)
class _Candidate:
    row_id: int
    key: str
    brand_id: int
    name: str
    metrics: ScopeMetrics
    supply_at: int
    sold_after: int
    forward_sell_through: float | None


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman ρ with average ranks for ties; None without two pairs or without spread."""

    if len(xs) != len(ys) or len(xs) < 2:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        average = (start + end) / 2 + 1
        for position in range(start, end + 1):
            ranks[order[position]] = average
        start = end + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    spread_x = sum((x - mean_x) ** 2 for x in xs)
    spread_y = sum((y - mean_y) ** 2 for y in ys)
    if spread_x == 0 or spread_y == 0:
        return None
    return covariance / math.sqrt(spread_x * spread_y)


async def run_backtest(
    session: AsyncSession, *, as_of_days: int = AS_OF_DAYS, now: datetime | None = None
) -> BacktestReport:
    """Score every brand's groups as of ``now - as_of_days`` against the days since."""

    if as_of_days < 1:
        raise ValueError("as_of_days must be at least 1")
    moment = now or datetime.now(UTC)
    as_of = moment - timedelta(days=as_of_days)
    horizon = min(as_of + timedelta(days=FORWARD_DAYS), moment)
    service = MetricsService(session)
    sections = {type_id: item.section for type_id, item in load_policy().taxonomy.types.items()}
    policy = await effective_policy(session)
    brand_ids = list(await session.scalars(select(Brand.id).order_by(Brand.id)))
    rows: list[RankRow] = []
    pool: list[LiquidityCandidate] = []
    candidates: list[_Candidate] = []
    for brand_id in brand_ids:
        past = await service.scopes(brand_id, as_of, historical=True)
        present = await service.scopes(brand_id, moment)
        sold_at = _sales(present)
        for key, items in past.by_scope.items():
            metrics = compute_metrics(items, as_of)
            row = _rank_row(len(rows), past, key, metrics)
            rows.append(row)
            if _scored(past, key, row):
                section = sections.get(row.product_type) if row.product_type else None
                pool.append(_liquidity(row.id, section, metrics, policy))
            if not _evaluated(past, key, metrics, row):
                continue
            active = [item for item in items if item.status == "active"]
            candidates.append(
                _Candidate(
                    row_id=row.id,
                    key=key,
                    brand_id=brand_id,
                    name=past.groups[past.scope_meta[key]["group_id"]].name,
                    metrics=metrics,
                    supply_at=len(active),
                    sold_after=_sold_between(present.by_scope.get(key, []), as_of, moment),
                    forward_sell_through=_forward(active, sold_at, as_of, horizon),
                )
            )
    p30 = rank(rows)
    scores = liquidity_scores(pool, policy)
    outcomes = tuple(_outcome(candidate, p30, scores) for candidate in candidates)
    return BacktestReport(
        as_of=as_of,
        now=moment,
        horizon=horizon,
        brands=len(brand_ids),
        outcomes=outcomes,
        volume=_correlations(outcomes, VOLUME),
        velocity=_correlations(outcomes, VELOCITY),
    )


def _sales(scopes: BrandScopes) -> dict[int, datetime]:
    return {
        item.id: item.sold_at
        for items in scopes.by_scope.values()
        for item in items
        if item.status == "sold" and item.sold_at is not None
    }


def _rank_row(row_id: int, scopes: BrandScopes, key: str, metrics: ScopeMetrics) -> RankRow:
    meta = scopes.scope_meta[key]
    group = scopes.groups.get(meta["group_id"]) if "group_id" in meta else None
    return RankRow(
        id=row_id,
        scope=meta["scope"],
        brand_id=meta["brand_id"],
        product_type=meta.get("product_type"),
        p30_sold=metrics.p30_sold,
        p30_cohort=metrics.p30_cohort,
        retired=group is not None and group.retired_at is not None,
    )


def _liquidity(
    row_id: int, section: str | None, metrics: ScopeMetrics, policy: LiquidityPolicy
) -> LiquidityCandidate:
    windows: dict[str, Any] = dict(metrics.windows)
    return liquidity_candidate(row_id, section, windows, policy.period_days)


def _scored(scopes: BrandScopes, key: str, row: RankRow) -> bool:
    """The model rows a recompute ranks against each other (``service._ranked_rows``)."""

    meta = scopes.scope_meta[key]
    if row.scope != "model" or row.retired or meta.get("is_fallback", False):
        return False
    if not meta.get("is_line", False) or row.product_type == REVIEW_TYPE:
        return False
    return not scopes.groups[meta["group_id"]].slug.startswith(DESCRIPTOR_PREFIX)


def _evaluated(scopes: BrandScopes, key: str, metrics: ScopeMetrics, row: RankRow) -> bool:
    meta = scopes.scope_meta[key]
    if row.scope != "model" or row.retired or meta.get("is_fallback", False):
        return False
    if metrics.listings < MIN_LISTINGS:
        return False
    return scopes.groups[meta["group_id"]].status != "ignored"


def _sold_between(items: Sequence[MetricListing], start: datetime, end: datetime) -> int:
    return sum(
        item.status == "sold" and item.sold_at is not None and start < item.sold_at <= end
        for item in items
    )


def _forward(
    active: Sequence[MetricListing],
    sold_at: dict[int, datetime],
    start: datetime,
    end: datetime,
) -> float | None:
    if len(active) < MIN_LISTINGS:
        return None
    sold = sum(start < sold_at[item.id] <= end for item in active if item.id in sold_at)
    return sold / len(active)


def _outcome(
    candidate: _Candidate, p30: dict[int, Decimal], scores: dict[int, Decimal]
) -> Outcome:
    metrics = candidate.metrics
    cohort = metrics.p30_cohort
    return Outcome(
        key=candidate.key,
        brand_id=candidate.brand_id,
        name=candidate.name,
        listings_at=metrics.listings,
        supply_at=candidate.supply_at,
        sold_after=candidate.sold_after,
        forward_sell_through=candidate.forward_sell_through,
        sold_30d=metrics.sold_30d,
        sold_90d=metrics.sold_90d,
        sell_through_30d=metrics.sell_through_30d,
        p30_raw=Decimal(metrics.p30_sold) / cohort if cohort else None,
        p30=p30.get(candidate.row_id),
        liquidity_score=scores.get(candidate.row_id),
    )


def _number(value: Decimal | float | int | None) -> float:
    return float(value) if value is not None else 0.0


_Metric = tuple[str, str, Callable[[Outcome], float]]
_VOLUME_METRICS: tuple[_Metric, ...] = (
    ("depth", NEW, lambda outcome: math.log1p(outcome.sold_90d)),
    ("liquidity_score", NEW, lambda outcome: _number(outcome.liquidity_score)),
    ("sold_30d", BASELINE, lambda outcome: float(outcome.sold_30d)),
)
_VELOCITY_METRICS: tuple[_Metric, ...] = (
    ("p30", NEW, lambda outcome: _number(outcome.p30)),
    ("liquidity_score", NEW, lambda outcome: _number(outcome.liquidity_score)),
    ("p30_raw", INFO, lambda outcome: _number(outcome.p30_raw)),
    ("sell_through_30d", BASELINE, lambda outcome: _number(outcome.sell_through_30d)),
)


def _correlations(outcomes: Sequence[Outcome], target: str) -> tuple[Correlation, ...]:
    if target == VOLUME:
        sample = list(outcomes)
        actual = [float(outcome.sold_after) for outcome in sample]
        metrics = _VOLUME_METRICS
    else:
        sample = [outcome for outcome in outcomes if outcome.forward_sell_through is not None]
        actual = [outcome.forward_sell_through or 0.0 for outcome in sample]
        metrics = _VELOCITY_METRICS
    return tuple(
        Correlation(name, role, spearman([value(item) for item in sample], actual), len(sample))
        for name, role, value in metrics
    )


def _verdicts(target: str, correlations: Sequence[Correlation]) -> list[Verdict]:
    baselines = [item for item in correlations if item.role == BASELINE and item.rho is not None]
    best = max(baselines, key=lambda item: item.rho or 0.0, default=None)
    verdicts = []
    for item in correlations:
        if item.role != NEW:
            continue
        passed = None
        if item.rho is not None:
            passed = best is None or item.rho >= (best.rho or 0.0) - GATE_TOLERANCE
        verdicts.append(
            Verdict(
                target,
                item.metric,
                item.rho,
                best.metric if best else None,
                best.rho if best else None,
                passed,
            )
        )
    return verdicts


def write_report(report: BacktestReport, directory: Path) -> Path:
    """``backtest-<date>.md`` in ``directory``; a same-day rerun replaces it."""

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"backtest-{report.now.date().isoformat()}.md"
    path.write_text(render_report(report), encoding="utf-8")
    return path


def render_report(report: BacktestReport) -> str:
    days = (report.now - report.as_of).days
    velocity_groups = sum(item.forward_sell_through is not None for item in report.outcomes)
    lines = [
        f"# Бэктест метрик ({METRICS_VERSION}) — {report.now.date().isoformat()}",
        "",
        f"- Момент оценки T = now − {days} дн = {report.as_of:%Y-%m-%d %H:%M} UTC.",
        f"- Объём: продажи группы в (T, now], now = {report.now:%Y-%m-%d %H:%M} UTC.",
        f"- Скорость: доля предложения на T, проданная в (T, {report.horizon:%Y-%m-%d}].",
        f"- Брендов: {report.brands}; групп с ≥ {MIN_LISTINGS} объявлениями на T: "
        f"{len(report.outcomes)}; из них с ≥ {MIN_LISTINGS} в продаже на T: {velocity_groups}.",
        f"- Метрика — Spearman ρ (средние ранги для связок); ворота: новая оценка проходит, "
        f"если её ρ ≥ ρ лучшей базовой − {GATE_TOLERANCE}.",
        "",
        "## Объём: продажи в (T, now]",
        "",
        *_table(report.volume),
        "",
        "## Скорость: forward sell-through",
        "",
        *_table(report.velocity),
        "",
        "## Вердикт",
        "",
        *_verdict_lines(report.verdicts),
        "",
        f"## Топ-{_TOP_GROUPS} групп по LS на T",
        "",
        *_top_groups(report.outcomes),
        "",
        "## Ограничения",
        "",
        "- Группировка — нынешняя: группы, найденные после T, видны и на T (утечка состава).",
        "- Удалённые лоты до начала наблюдений неизвестны (то же смещение, что в 2.2).",
        "- Цена активного лота — текущая; изменения цены после T в истории не откатываются.",
        "- Метрики на T берут только продажи ≤ T, но снятые с продажи лоты без времени "
        "проверки остаются снятыми и на T.",
        "",
    ]
    return "\n".join(lines)


def _rho(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _table(correlations: Sequence[Correlation]) -> list[str]:
    roles = {NEW: "новая", BASELINE: "базовая", INFO: "справочно"}
    return [
        "| Метрика | Роль | Spearman ρ | Групп |",
        "|---|---|---:|---:|",
        *(
            f"| `{item.metric}` | {roles[item.role]} | {_rho(item.rho)} | {item.groups} |"
            for item in correlations
        ),
    ]


def _verdict_lines(verdicts: Sequence[Verdict]) -> list[str]:
    if not verdicts:
        return ["Нет групп для оценки."]
    lines = []
    for item in verdicts:
        status = {True: "проходит", False: "не проходит", None: "не определено"}[item.passed]
        against = f"`{item.baseline}` ρ = {_rho(item.baseline_rho)}" if item.baseline else "—"
        lines.append(
            f"- {item.target}: `{item.metric}` ρ = {_rho(item.rho)} против {against} — "
            f"**{status}**."
        )
    return lines


def _top_groups(outcomes: Sequence[Outcome]) -> list[str]:
    ordered = sorted(outcomes, key=lambda item: _number(item.liquidity_score), reverse=True)
    return [
        "| Группа | LS | P̃30 | sold_30d | На продаже T | Продано после T | Forward ST |",
        "|---|---:|---:|---:|---:|---:|---:|",
        *(
            f"| {item.name} | {_number(item.liquidity_score):.1f} | {_number(item.p30):.3f} "
            f"| {item.sold_30d} | {item.supply_at} | {item.sold_after} "
            f"| {_share(item.forward_sell_through)} |"
            for item in ordered[:_TOP_GROUPS]
        ),
    ]


def _share(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"
