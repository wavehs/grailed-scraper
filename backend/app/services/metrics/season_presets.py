"""Wardrobe season presets (v2 plan 3.3): product types that sell above the market in a season.

``config/seasons.yaml`` lists them per season. ``seasons-suggest`` proposes the lists from the
type profiles (``SI_rel ≥ 1.15``); the file is then edited by hand. It is kept apart from
``taxonomy.yaml`` because that file is part of the grouping digest: editing a preset there
would regroup every brand.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PROJECT_ROOT
from app.db.models import SeasonProfile
from app.services.grouping.policy import load_policy
from app.services.metrics.seasonality import SEASONS

SEASONS_PATH = PROJECT_ROOT / "config" / "seasons.yaml"
SI_REL_MIN = 1.15
# Types that are no wardrobe item: unsorted listings and printed matter.
_EXCLUDED_SECTIONS = frozenset({"other"})


@dataclass(frozen=True, slots=True)
class SeasonCandidate:
    product_type: str
    si_rel: float
    sales: float


def suggest_presets(
    profiles: Iterable[tuple[str, Mapping[str, Any]]],
    allowed: Iterable[str],
    *,
    minimum: float = SI_REL_MIN,
) -> dict[str, list[SeasonCandidate]]:
    """Types whose season ``si_rel`` reaches ``minimum``, strongest first, per season.

    ``profiles`` are ``(product_type, seasons JSON)`` of the all-department type profiles;
    a season without enough sales is ``None`` there and never suggested.
    """

    known = set(allowed)
    suggestion: dict[str, list[SeasonCandidate]] = {season: [] for season in SEASONS}
    for product_type, seasons in profiles:
        if product_type not in known:
            continue
        for season in SEASONS:
            point = seasons.get(season)
            if not point:
                continue
            si_rel = point.get("si_rel")
            if si_rel is not None and float(si_rel) >= minimum:
                candidate = SeasonCandidate(product_type, float(si_rel), float(point["sales"]))
                suggestion[season].append(candidate)
    for candidates in suggestion.values():
        candidates.sort(key=lambda item: (-item.si_rel, item.product_type))
    return suggestion


def render_yaml(suggestion: Mapping[str, list[SeasonCandidate]], minimum: float) -> str:
    """The ``seasons:`` block to paste into ``config/seasons.yaml``, with the evidence."""

    lines = [
        f"# Suggested by `seasons-suggest`: SI_rel >= {minimum:g} over all departments.",
        "seasons:",
    ]
    for season in SEASONS:
        candidates = suggestion.get(season, [])
        if not candidates:
            lines.append(f"  {season}: []")
            continue
        lines.append(f"  {season}:")
        lines.extend(
            f"    - {item.product_type}  # SI_rel {item.si_rel:.2f}, {item.sales:.0f} sales"
            for item in candidates
        )
    return "\n".join(lines) + "\n"


async def type_profiles(session: AsyncSession) -> list[tuple[str, dict[str, Any]]]:
    """Seasons of every type profile that spans all departments."""

    rows = await session.execute(
        select(SeasonProfile.product_type, SeasonProfile.seasons).where(
            SeasonProfile.kind == "type",
            SeasonProfile.department.is_(None),
            SeasonProfile.product_type.is_not(None),
        )
    )
    return [(str(product_type), seasons) for product_type, seasons in rows.tuples()]


def wardrobe_types() -> list[str]:
    """Taxonomy types a preset may list."""

    types = load_policy().taxonomy.types.values()
    return [item.id for item in types if item.section not in _EXCLUDED_SECTIONS]


@lru_cache(maxsize=4)
def load_season_presets(path: Path = SEASONS_PATH) -> dict[str, tuple[str, ...]]:
    """Validated presets once per process; a missing file means no presets."""

    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    seasons = payload.get("seasons") if isinstance(payload, dict) else None
    if not isinstance(seasons, dict):
        raise ValueError(f"{path.name}: expected a 'seasons' mapping")
    known = set(wardrobe_types())
    presets: dict[str, tuple[str, ...]] = {}
    for season, types in seasons.items():
        if season not in SEASONS:
            raise ValueError(f"{path.name}: unknown season {season!r}")
        if not isinstance(types, list) or not all(isinstance(item, str) for item in types):
            raise ValueError(f"{path.name}: {season} must list type ids")
        unknown = sorted(set(types) - known)
        if unknown:
            raise ValueError(f"{path.name}: {season} lists unknown types {unknown}")
        presets[season] = tuple(dict.fromkeys(types))
    return presets
