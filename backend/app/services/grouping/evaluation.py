"""grouping-eval: measure a brand's grouping against hand labels kept in ``eval/<brand>/``.

Labels are listings (``labels.csv``) and phrases (``phrases.csv``); titles always come from
the database, so the files hold only public ids and verdicts. Definitions: docs/TESTING.md.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PROJECT_ROOT
from app.db.models import Brand, Listing, ListingModelAssignment, ModelGroup
from app.services.grouping.kinds import GroupKind, collab_partner, group_kind
from app.services.grouping.policy import REVIEW_TYPE
from app.services.grouping.text import fold, singular

EVAL_DIRECTORY = PROJECT_ROOT / "eval"
# Files sealed in MANIFEST: the labels and the accepted spellings of canonical models.
LABEL_FILES = ("labels.csv", "phrases.csv", "canon.csv")
LABEL_COLUMNS = ("grailed_id", "gold_type", "gold_model", "gold_collab", "borderline", "note")
PHRASE_COLUMNS = ("product_type", "slug", "name", "verdict", "note")
JOURNAL_COLUMNS = (
    "recorded_at",
    "checkpoint",
    "commit",
    "dirty",
    "labels_sha256",
    "phrases_sha256",
    "rows",
    "passed",
    "aggregates",
)
# sha256(grailed_id) % 5 in {0, 1}: about 40% of the labels are held out.
HOLDOUT_BUCKETS = frozenset({0, 1})
# Pass rule for sampled shares: the point meets the target and the 95% Wilson bound is
# within this margin of it.
WILSON_MARGIN = 0.05
_Z95 = 1.959963984540054
_TOKEN = re.compile(r"[^\W_]+")

Split = Literal["dev", "holdout", "all"]
GoldKind = Literal["model", "none", "descriptor"]
VerdictKind = Literal["model", "not_model", "duplicate", "borderline"]


# --- Labels -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gold:
    """``City``, ``City > Mini City`` (line > version), ``NONE`` or ``DESCRIPTOR:<class>``."""

    kind: GoldKind
    line: str | None = None
    leaf: str | None = None
    descriptor: str | None = None


@dataclass(frozen=True, slots=True)
class Label:
    grailed_id: int
    gold_type: str
    gold: Gold
    collab: str
    borderline: bool
    note: str = ""

    @property
    def collab_only(self) -> bool:
        """A collaboration without a model: measured by the collab metrics only."""

        return bool(self.collab) and self.gold.kind != "model"


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: VerdictKind
    detail: str = ""

    @property
    def garbage(self) -> bool:
        return self.kind == "not_model"


def parse_gold(value: str) -> Gold:
    text = value.strip()
    head = text.casefold()
    if head == "none":
        return Gold("none")
    if head.startswith("descriptor"):
        _, _, detail = text.partition(":")
        return Gold("descriptor", descriptor=detail.strip().casefold() or None)
    line, separator, leaf = text.partition(">")
    line = line.strip()
    leaf = leaf.strip() if separator else line
    if not line or not leaf:
        raise ValueError(f"Bad gold_model {value!r}")
    return Gold("model", line=line, leaf=leaf)


def parse_verdict(value: str) -> Verdict:
    head, _, detail = value.strip().partition(":")
    kind = head.strip().casefold()
    if kind not in {"model", "not_model", "duplicate", "borderline"}:
        raise ValueError(f"Bad phrase verdict {value!r}")
    if kind == "duplicate" and not detail.strip():
        raise ValueError("A duplicate verdict names its canonical model: duplicate:<name>")
    return Verdict(kind, detail.strip())  # type: ignore[arg-type]


def load_labels(path: Path) -> list[Label]:
    labels: list[Label] = []
    seen: set[int] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            try:
                grailed_id = int(row["grailed_id"])
                gold = parse_gold(row["gold_model"] or "")
            except (KeyError, ValueError) as exc:
                raise ValueError(f"{path.name}:{line}: {exc}") from exc
            if grailed_id in seen:
                raise ValueError(f"{path.name}:{line}: duplicate grailed_id {grailed_id}")
            seen.add(grailed_id)
            labels.append(
                Label(
                    grailed_id=grailed_id,
                    gold_type=(row.get("gold_type") or "").strip(),
                    gold=gold,
                    collab=(row.get("gold_collab") or "").strip(),
                    borderline=(row.get("borderline") or "0").strip() == "1",
                    note=(row.get("note") or "").strip(),
                )
            )
    return labels


def load_phrases(path: Path) -> dict[tuple[str, str], Verdict]:
    verdicts: dict[tuple[str, str], Verdict] = {}
    if not path.is_file():
        return verdicts
    with path.open(encoding="utf-8", newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            key = (row["product_type"].strip(), row["slug"].strip())
            try:
                verdicts[key] = parse_verdict(row["verdict"] or "")
            except ValueError as exc:
                raise ValueError(f"{path.name}:{line}: {exc}") from exc
    return verdicts


def split_of(grailed_id: int) -> Literal["dev", "holdout"]:
    bucket = int(hashlib.sha256(str(grailed_id).encode()).hexdigest(), 16) % 5
    return "holdout" if bucket in HOLDOUT_BUCKETS else "dev"


def sample_key(grailed_id: int) -> str:
    """Uniform, stable sampling order, independent of the dev/holdout split."""

    return hashlib.sha256(f"sample:{grailed_id}".encode()).hexdigest()


def name_keys(text: str) -> frozenset[str]:
    """Spelling-insensitive keys: "Speed Hunters", "Speedhunters" and "speed-hunters" match."""

    tokens = _TOKEN.findall(fold(text))
    keys = {"".join(tokens), "".join(singular(token) for token in tokens)}
    return frozenset(key for key in keys if key)


# --- Database snapshot ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroupView:
    id: int
    product_type: str
    slug: str
    name: str
    aliases: tuple[str, ...]
    parent_id: int | None
    status: str
    source: str

    @property
    def kind(self) -> GroupKind:
        return group_kind(self.slug, self.product_type)


@dataclass(frozen=True, slots=True)
class ListingView:
    grailed_id: int
    status: str
    title: str
    category_path: str | None
    designers: tuple[str, ...]
    product_type: str | None
    seller: str | None
    group_id: int | None

    @property
    def sold(self) -> bool:
        return self.status == "sold"


@dataclass(slots=True)
class Snapshot:
    brand: str
    groups: dict[int, GroupView]
    listings: dict[int, ListingView]
    _keys: dict[int, frozenset[str]] = field(default_factory=dict)

    def line_of(self, group: GroupView) -> GroupView:
        parent = self.groups.get(group.parent_id) if group.parent_id is not None else None
        return parent or group

    def keys(self, group: GroupView) -> frozenset[str]:
        if group.id not in self._keys:
            found: set[str] = set()
            for value in (group.name, *group.aliases, group.slug.replace("-", " ")):
                found |= name_keys(value)
            self._keys[group.id] = frozenset(found)
        return self._keys[group.id]

    def placement(self, grailed_id: int) -> Placement | None:
        listing = self.listings.get(grailed_id)
        if listing is None:
            return None
        group = self.groups.get(listing.group_id) if listing.group_id is not None else None
        if group is None:
            kind: GroupKind = "review" if listing.product_type == REVIEW_TYPE else "none"
            return Placement(kind, listing.product_type or "", None, None, frozenset(), frozenset())
        line = self.line_of(group)
        return Placement(
            kind=group.kind,
            product_type=group.product_type,
            group=group,
            line=line,
            line_keys=self.keys(line),
            leaf_keys=self.keys(group),
            partner=collab_partner(group.slug) or "",
        )


@dataclass(frozen=True, slots=True)
class Placement:
    kind: GroupKind
    product_type: str
    group: GroupView | None
    line: GroupView | None
    line_keys: frozenset[str]
    leaf_keys: frozenset[str]
    partner: str = ""


async def load_snapshot(session: AsyncSession, brand_slug: str) -> Snapshot:
    brand = await session.scalar(select(Brand).where(Brand.slug == brand_slug))
    if brand is None:
        raise RuntimeError(f"Unknown brand {brand_slug}")
    groups = {
        group.id: GroupView(
            id=group.id,
            product_type=group.product_type,
            slug=group.slug,
            name=group.name,
            aliases=tuple(group.aliases or ()),
            parent_id=group.parent_id,
            status=group.status,
            source=group.source,
        )
        for group in await session.scalars(
            select(ModelGroup).where(ModelGroup.brand_id == brand.id)
        )
    }
    statement = (
        select(
            Listing.grailed_id,
            Listing.status,
            Listing.title,
            Listing.category_path,
            Listing.designer_names,
            Listing.product_type,
            Listing.seller_identity,
            ListingModelAssignment.model_group_id,
        )
        .outerjoin(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
        .where(Listing.brand_id == brand.id)
    )
    listings = {
        row[0]: ListingView(
            grailed_id=row[0],
            status=row[1],
            title=row[2],
            category_path=row[3],
            designers=tuple(row[4] or ()),
            product_type=row[5],
            seller=row[6],
            group_id=row[7],
        )
        for row in await session.execute(statement)
    }
    return Snapshot(brand_slug, groups, listings)


# --- Shares -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Share:
    hits: int
    total: int

    @property
    def point(self) -> float | None:
        return self.hits / self.total if self.total else None

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.hits, self.total)

    def as_dict(self) -> dict[str, Any]:
        interval = self.interval
        return {
            "hits": self.hits,
            "total": self.total,
            "point": _round(self.point),
            "low": _round(interval[0]) if interval else None,
            "high": _round(interval[1]) if interval else None,
        }


def wilson(hits: int, total: int, z: float = _Z95) -> tuple[float, float] | None:
    if total <= 0:
        return None
    share = hits / total
    denominator = 1 + z * z / total
    centre = (share + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total))
    margin /= denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


# --- Listing metrics --------------------------------------------------------------------


Canon = Mapping[str, frozenset[str]]


def load_canon(path: Path) -> dict[str, frozenset[str]]:
    """``canon.csv``: a canonical model and its accepted spellings (rule 6), never fragments."""

    canon: dict[str, frozenset[str]] = {}
    if not path.is_file():
        return canon
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            names = [row["canon"], *(item for item in row["aliases"].split("|") if item.strip())]
            keys = frozenset(key for name in names for key in name_keys(name))
            for key in keys:
                canon[key] = keys
    return canon


def gold_keys(name: str, canon: Canon | None = None) -> frozenset[str]:
    keys = name_keys(name)
    for key in keys:
        if canon and key in canon:
            return canon[key]
    return keys


def line_correct(
    label: Label,
    placement: Placement | None,
    *,
    strict: bool = False,
    canon: Canon | None = None,
) -> bool:
    if placement is None or placement.kind != "model" or label.gold.kind != "model":
        return False
    gold = label.gold.leaf if strict else label.gold.line
    keys = placement.leaf_keys if strict else placement.line_keys
    return bool(gold_keys(gold or "", canon) & keys)


def listing_metrics(
    labels: Sequence[Label],
    current: Snapshot,
    baseline: Snapshot | None = None,
    canon: Canon | None = None,
) -> dict[str, Any]:
    """Shares over labeled listings; borderline rows are reported apart (docs/TESTING.md)."""

    missing = [label.grailed_id for label in labels if label.grailed_id not in current.listings]
    usable = [label for label in labels if label.grailed_id in current.listings]
    main = [label for label in usable if not label.borderline]
    counters: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def count(name: str, hit: bool) -> None:
        counters[name][1] += 1
        counters[name][0] += int(hit)

    review = 0
    for label in main:
        placement = current.placement(label.grailed_id)
        assert placement is not None
        is_model = label.gold.kind == "model"
        correct = line_correct(label, placement, canon=canon)
        if placement.kind == "review":
            review += 1
        if placement.kind == "model" and not label.collab_only:
            count("model_precision", correct)
            count(
                "model_precision_strict",
                line_correct(label, placement, strict=True, canon=canon),
            )
        if is_model:
            count("recall_line", correct)
        if placement.kind in {"none", "descriptor"}:
            count("none_precision", not is_model and not label.collab)
        if placement.kind == "descriptor":
            count("descriptor_precision", label.gold.kind == "descriptor" and not label.collab)
        if placement.kind == "collab":
            count(
                "collab_precision",
                label.collab_only and _partner_key(label.collab) == placement.partner,
            )
        if label.collab_only:
            count(
                "collab_recall",
                placement.kind == "collab" and _partner_key(label.collab) == placement.partner,
            )
        if label.gold_type:
            count("type_agreement", label.gold_type == placement.product_type)
        if baseline is not None and line_correct(
            label, baseline.placement(label.grailed_id), canon=canon
        ):
            count("recall_regression", correct)
    shares = {name: Share(*values) for name, values in sorted(counters.items())}
    borderline = [label for label in usable if label.borderline]
    return {
        "rows": len(labels),
        "used": len(main),
        "missing": len(missing),
        "missing_ids": missing[:20],
        "borderline": len(borderline),
        "review": review,
        "shares": shares,
        "borderline_rows": [
            _borderline_row(label, current.placement(label.grailed_id)) for label in borderline
        ],
    }


def _borderline_row(label: Label, placement: Placement | None) -> dict[str, Any]:
    group = placement.group if placement else None
    return {
        "grailed_id": label.grailed_id,
        "gold": label.gold.leaf or label.gold.kind,
        "system": group.name if group else None,
        "kind": placement.kind if placement else None,
        "note": label.note,
    }


def _partner_key(value: str) -> str:
    return "-".join(_TOKEN.findall(fold(value)))


# --- Phrase metrics ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineStat:
    line: GroupView
    sales: int
    listings: int

    @property
    def key(self) -> tuple[str, str]:
        return self.line.product_type, self.line.slug


def rank_lines(snapshot: Snapshot) -> list[LineStat]:
    """Model lines (versions included) by sales; descriptors and service groups are not models."""

    sales: Counter[int] = Counter()
    listings: Counter[int] = Counter()
    for listing in snapshot.listings.values():
        group = snapshot.groups.get(listing.group_id) if listing.group_id is not None else None
        if group is None:
            continue
        line = snapshot.line_of(group)
        if line.kind != "model":
            continue
        listings[line.id] += 1
        sales[line.id] += int(listing.sold)
    stats = [
        LineStat(snapshot.groups[line_id], sales[line_id], count)
        for line_id, count in listings.items()
    ]
    stats.sort(key=lambda item: (-item.sales, -item.listings, item.key))
    return stats


def phrase_metrics(
    lines: Sequence[LineStat], verdicts: Mapping[tuple[str, str], Verdict], top: int
) -> dict[str, Any]:
    selected = lines[:top]
    total_sales = sum(item.sales for item in selected)
    tally: Counter[str] = Counter()
    sales: Counter[str] = Counter()
    unlabeled: list[dict[str, Any]] = []
    for rank, item in enumerate(selected, start=1):
        verdict = verdicts.get(item.key)
        kind = verdict.kind if verdict else "unlabeled"
        tally[kind] += 1
        sales[kind] += item.sales
        if verdict is None:
            unlabeled.append(_line_ref(item, rank))
    garbage = tally["not_model"]
    duplicate = tally["duplicate"]
    return {
        "lines": len(selected),
        "sales": total_sales,
        "counts": dict(tally),
        "garbage_lines": _ratio(garbage, len(selected)),
        "garbage_sales": _ratio(sales["not_model"], total_sales),
        "garbage_dup_lines": _ratio(garbage + duplicate, len(selected)),
        "garbage_dup_sales": _ratio(sales["not_model"] + sales["duplicate"], total_sales),
        "unlabeled": unlabeled,
        "listed": [
            {**_line_ref(item, rank), "verdict": _verdict_text(verdicts.get(item.key))}
            for rank, item in enumerate(selected, start=1)
            if verdicts.get(item.key) is None or verdicts[item.key].kind != "model"
        ],
    }


def tail_sample(lines: Sequence[LineStat], *, after: int, size: int, seed: int) -> list[LineStat]:
    """Lines past ``after`` drawn with replacement in proportion to sales.

    The share of garbage among the draws estimates the garbage share of tail sales.
    """

    tail = [item for item in lines[after:] if item.sales > 0]
    if not tail:
        return []
    return random.Random(seed).choices(tail, weights=[item.sales for item in tail], k=size)


def tail_metrics(
    sample: Sequence[LineStat], verdicts: Mapping[tuple[str, str], Verdict]
) -> dict[str, Any]:
    labeled = [(item, verdicts[item.key]) for item in sample if item.key in verdicts]
    unlabeled = sorted({item.key for item in sample if item.key not in verdicts})
    garbage = Share(sum(verdict.garbage for _, verdict in labeled), len(labeled))
    garbage_dup = Share(
        sum(verdict.kind in {"not_model", "duplicate"} for _, verdict in labeled), len(labeled)
    )
    return {
        "draws": len(sample),
        "labeled": len(labeled),
        "garbage_sales": garbage,
        "garbage_dup_sales": garbage_dup,
        "unlabeled": [{"product_type": key[0], "slug": key[1]} for key in unlabeled],
    }


def _line_ref(item: LineStat, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "product_type": item.line.product_type,
        "slug": item.line.slug,
        "name": item.line.name,
        "status": item.line.status,
        "sales": item.sales,
        "listings": item.listings,
    }


def _verdict_text(verdict: Verdict | None) -> str:
    if verdict is None:
        return "unlabeled"
    return f"{verdict.kind}:{verdict.detail}" if verdict.detail else verdict.kind


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


# --- Collaborations and lost groups -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CollabWatch:
    name: str
    designers: frozenset[str]
    title: tuple[str, ...]

    def matches(self, listing: ListingView) -> bool:
        if any(fold(name) in self.designers for name in listing.designers):
            return True
        title = " ".join(_TOKEN.findall(fold(listing.title)))
        return any(f" {phrase} " in f" {title} " for phrase in self.title)


def collab_watch_metrics(snapshot: Snapshot, watches: Sequence[CollabWatch]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for watch in watches:
        kinds: Counter[str] = Counter()
        for listing in snapshot.listings.values():
            if listing.sold and watch.matches(listing):
                placement = snapshot.placement(listing.grailed_id)
                kinds[placement.kind if placement else "none"] += 1
        total = sum(kinds.values())
        result[watch.name] = {
            "sales": total,
            "by_kind": dict(kinds),
            "none_sales": kinds["none"],
            "none_share": _ratio(kinds["none"], total),
        }
    return result


def lost_groups(
    baseline: Snapshot,
    current: Snapshot,
    verdicts: Mapping[tuple[str, str], Verdict],
    *,
    labeled_from: int = 10,
    examples: int = 5,
) -> dict[str, Any]:
    """Former model groups that are gone now and whose sales got no model line."""

    present = {
        (group.product_type, group.slug)
        for group in current.groups.values()
        if group.kind == "model" and group.status != "ignored"
    }
    lost: dict[int, list[ListingView]] = defaultdict(list)
    for listing in baseline.listings.values():
        if not listing.sold or listing.group_id is None:
            continue
        group = baseline.groups.get(listing.group_id)
        if group is None or group.kind != "model":
            continue
        if (group.product_type, group.slug) in present:
            continue
        placement = current.placement(listing.grailed_id)
        if placement is not None and placement.kind in {"none", "descriptor", "review"}:
            lost[group.id].append(listing)
    rows: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    for group_id, items in sorted(lost.items(), key=lambda item: (-len(item[1]), item[0])):
        group = baseline.groups[group_id]
        verdict = verdicts.get((group.product_type, group.slug))
        status = _lost_status(verdict)
        totals[status] += len(items)
        rows.append(
            {
                "product_type": group.product_type,
                "slug": group.slug,
                "name": group.name,
                "lost_sales": len(items),
                "status": status,
                "examples": [item.title for item in items[:examples]],
            }
        )
    unlabeled_large = [
        row for row in rows if row["status"] == "unlabeled" and row["lost_sales"] >= labeled_from
    ]
    return {
        "groups": len(rows),
        "lost_sales": sum(totals.values()),
        "by_status": dict(totals),
        "loss_sales": totals["loss"],
        "unlabeled_large": len(unlabeled_large),
        "rows": rows,
    }


def _lost_status(verdict: Verdict | None) -> str:
    if verdict is None:
        return "unlabeled"
    if verdict.kind == "model":
        return "loss"
    return {"not_model": "garbage", "duplicate": "garbage", "borderline": "borderline"}[
        verdict.kind
    ]


# --- Configuration, manifest and journal ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Target:
    metric: str
    bound: Literal["min", "max"]
    value: float | Literal["baseline"]


@dataclass(frozen=True, slots=True)
class EvalConfig:
    brand: str
    checkpoints: tuple[str, ...]
    top: tuple[int, ...]
    strict_top: int
    tail_after: int
    tail_size: int
    tail_seed: int
    lost_labeled_from: int
    loss_limit: int
    watches: tuple[CollabWatch, ...]
    targets: dict[str, tuple[Target, ...]]


def load_config(directory: Path) -> EvalConfig:
    path = directory / "eval.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML object")
    tail = payload.get("tail") or {}
    lost = payload.get("lost") or {}
    targets: dict[str, tuple[Target, ...]] = {}
    for name, raw in (payload.get("targets") or {}).items():
        parsed: list[Target] = []
        for metric, rule in raw.items():
            ((bound, value),) = rule.items()
            if bound not in {"min", "max"}:
                raise ValueError(f"{path.name}: target {metric} needs min or max")
            parsed.append(
                Target(str(metric), bound, "baseline" if value == "baseline" else float(value))
            )
        targets[str(name)] = tuple(parsed)
    return EvalConfig(
        brand=str(payload["brand"]),
        checkpoints=tuple(str(item) for item in payload.get("checkpoints", ())),
        top=tuple(int(item) for item in payload.get("top", (50, 100))),
        strict_top=int(payload.get("strict_top", 100)),
        tail_after=int(tail.get("after", 200)),
        tail_size=int(tail.get("size", 50)),
        tail_seed=int(tail.get("seed", 0)),
        lost_labeled_from=int(lost.get("labeled_from", 10)),
        loss_limit=int(lost.get("loss_limit", 60)),
        watches=tuple(
            CollabWatch(
                name=str(name),
                designers=frozenset(fold(str(item)) for item in raw.get("designers", ())),
                title=tuple(
                    " ".join(_TOKEN.findall(fold(str(item)))) for item in raw.get("title", ())
                ),
            )
            for name, raw in (payload.get("collab_watch") or {}).items()
        ),
        targets=targets,
    )


def file_sha256(path: Path) -> str | None:
    """Line endings are normalized: a Windows checkout and CI must agree on the hash."""

    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def manifest_check(directory: Path) -> dict[str, Any]:
    path = directory / "MANIFEST"
    recorded: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        recorded = loaded if isinstance(loaded, dict) else {}
    files = recorded.get("files") or {}
    actual = {name: file_sha256(directory / name) for name in LABEL_FILES}
    return {
        "labels_sha256": actual["labels.csv"],
        "phrases_sha256": actual["phrases.csv"],
        "canon_sha256": actual["canon.csv"],
        "manifest": path.is_file(),
        "matches": bool(files) and all(files.get(name) == value for name, value in actual.items()),
        "labeler": recorded.get("labeler"),
        "external_check": recorded.get("external_check"),
    }


def seal_manifest(directory: Path, *, labeler: str, external_check: str) -> dict[str, Any]:
    payload = {
        "files": {
            name: file_sha256(directory / name) for name in LABEL_FILES
        },
        "date": datetime.now(UTC).date().isoformat(),
        "labeler": labeler,
        "external_check": external_check,
    }
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    with (directory / "MANIFEST").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return payload


def git_state(root: Path = PROJECT_ROOT) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True
    return commit, bool(status.strip())


def append_journal(directory: Path, entry: Mapping[str, Any]) -> None:
    path = directory / "holdout_runs.csv"
    exists = path.is_file()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=JOURNAL_COLUMNS, lineterminator="\n")
        if not exists:
            writer.writeheader()
        writer.writerow({name: entry.get(name, "") for name in JOURNAL_COLUMNS})


def journal_runs(directory: Path) -> int:
    path = directory / "holdout_runs.csv"
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


# --- Targets ----------------------------------------------------------------------------


def check_targets(
    targets: Sequence[Target], values: Mapping[str, Any], baseline: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Shares pass on the point and on the Wilson bound; enumerations on the value alone."""

    results: list[dict[str, Any]] = []
    for target in targets:
        value = values.get(target.metric)
        goal: float | None
        if target.value == "baseline":
            reference = baseline.get(target.metric)
            goal = _point(reference)
        else:
            goal = target.value
        point = _point(value)
        passed = point is not None and goal is not None
        if passed and point is not None and goal is not None:
            if target.bound == "min":
                passed = point >= goal
            else:
                passed = point <= goal
            if passed and isinstance(value, Share) and target.value != "baseline":
                interval = value.interval
                assert interval is not None
                if target.bound == "min":
                    passed = interval[0] >= goal - WILSON_MARGIN
                else:
                    passed = interval[1] <= goal + WILSON_MARGIN
        results.append(
            {
                "metric": target.metric,
                "bound": target.bound,
                "target": _round(goal),
                "value": value.as_dict() if isinstance(value, Share) else _round(point),
                "passed": passed,
            }
        )
    return results


def _point(value: Any) -> float | None:
    if isinstance(value, Share):
        return value.point
    if isinstance(value, int | float):
        return float(value)
    return None


def flat_values(report: Mapping[str, Any]) -> dict[str, Any]:
    """Metric paths used by targets: ``listings.model_precision``, ``top50.garbage_lines``…"""

    values: dict[str, Any] = {}
    for name, share in report["listings"]["shares"].items():
        values[f"listings.{name}"] = share
    for name, block in report["phrases"].items():
        if name == "tail":
            values["tail.garbage_sales"] = block["garbage_sales"]
            values["tail.garbage_dup_sales"] = block["garbage_dup_sales"]
            continue
        for metric in ("garbage_lines", "garbage_sales", "garbage_dup_lines", "garbage_dup_sales"):
            values[f"{name}.{metric}"] = block[metric]
    for name, block in report.get("collab_watch", {}).items():
        values[f"collab_watch.{name}.none_share"] = block["none_share"]
    lost = report.get("lost")
    if lost is not None:
        values["lost.loss_sales"] = lost["loss_sales"]
    return values


# --- Evaluation -------------------------------------------------------------------------


def evaluate(
    *,
    config: EvalConfig,
    directory: Path,
    current: Snapshot,
    baseline: Snapshot | None,
    split: Split,
    checkpoint: str | None,
    strict: bool,
) -> dict[str, Any]:
    if split == "holdout" and not checkpoint:
        raise ValueError("--split holdout needs --checkpoint")
    if checkpoint is not None and checkpoint not in config.checkpoints:
        raise ValueError(f"Unknown checkpoint {checkpoint!r}; eval.yaml lists {config.checkpoints}")
    labels_path = directory / "labels.csv"
    labels = load_labels(labels_path) if labels_path.is_file() else []
    selected = [
        label for label in labels if split == "all" or split_of(label.grailed_id) == split
    ]
    verdicts = load_phrases(directory / "phrases.csv")
    canon = load_canon(directory / "canon.csv")
    lines = rank_lines(current)
    phrases: dict[str, Any] = {
        f"top{size}": phrase_metrics(lines, verdicts, size) for size in config.top
    }
    sample = tail_sample(
        lines, after=config.tail_after, size=config.tail_size, seed=config.tail_seed
    )
    phrases["tail"] = tail_metrics(sample, verdicts)
    report: dict[str, Any] = {
        "brand": config.brand,
        "split": split,
        "checkpoint": checkpoint,
        "manifest": manifest_check(directory),
        "listings": listing_metrics(selected, current, baseline, canon),
        "phrases": phrases,
        "collab_watch": collab_watch_metrics(current, config.watches),
        "lost": (
            lost_groups(baseline, current, verdicts, labeled_from=config.lost_labeled_from)
            if baseline is not None
            else None
        ),
        "holdout_runs": journal_runs(directory),
    }
    baseline_values: dict[str, Any] = {}
    if baseline is not None:
        baseline_values = {
            f"listings.{name}": share
            for name, share in listing_metrics(selected, baseline, canon=canon)["shares"].items()
        }
        report["baseline_listings"] = {
            name.removeprefix("listings."): share for name, share in baseline_values.items()
        }
    targets = config.targets.get(checkpoint or "", ())
    report["targets"] = check_targets(targets, flat_values(report), baseline_values)
    problems = _problems(report, config, baseline is not None)
    report["warnings"] = problems
    external = report["manifest"].get("external_check")
    report["limitations"] = (
        [] if external and external != "none"
        else ["labels and audit come from one labeler; there is no external check (MANIFEST)"]
    )
    report["passed"] = all(item["passed"] for item in report["targets"]) and not (
        strict and problems
    )
    if split == "holdout" and checkpoint:
        commit, dirty = git_state()
        append_journal(
            directory,
            {
                "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "checkpoint": checkpoint,
                "commit": commit,
                "dirty": int(dirty),
                "labels_sha256": report["manifest"]["labels_sha256"],
                "phrases_sha256": report["manifest"]["phrases_sha256"],
                "rows": report["listings"]["used"],
                "passed": int(report["passed"]),
                "aggregates": json.dumps(
                    {
                        name: share.as_dict()["point"]
                        for name, share in report["listings"]["shares"].items()
                    },
                    sort_keys=True,
                ),
            },
        )
        report["holdout_runs"] = journal_runs(directory)
    return report


def _problems(report: Mapping[str, Any], config: EvalConfig, has_baseline: bool) -> list[str]:
    problems: list[str] = []
    manifest = report["manifest"]
    if not manifest["matches"]:
        problems.append("labels do not match MANIFEST sha256")
    for name, block in report["phrases"].items():
        if name == "tail":
            if block["unlabeled"]:
                problems.append(f"tail: {len(block['unlabeled'])} sampled lines are unlabeled")
            continue
        size = int(name.removeprefix("top"))
        if size <= config.strict_top and block["unlabeled"]:
            problems.append(f"{name}: {len(block['unlabeled'])} lines are unlabeled")
    if report["listings"]["missing"]:
        problems.append(f"{report['listings']['missing']} labeled listings are missing")
    if not has_baseline:
        problems.append("no --baseline-db: regression and lost groups are not measured")
    lost = report.get("lost")
    if lost is not None:
        if lost["unlabeled_large"]:
            problems.append(
                f"lost groups: {lost['unlabeled_large']} with ≥{config.lost_labeled_from} "
                "sales are unlabeled"
            )
        if lost["loss_sales"] > config.loss_limit:
            problems.append(
                f"lost groups: {lost['loss_sales']} sales of real models exceed "
                f"{config.loss_limit}"
            )
    return problems


def jsonable(value: Any) -> Any:
    if isinstance(value, Share):
        return value.as_dict()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]
    return value


# --- Labeling sheets --------------------------------------------------------------------

SHEET_LISTING_COLUMNS = ("grailed_id", "category_path", "designers", "title", *LABEL_COLUMNS[1:])
SHEET_PHRASE_COLUMNS = ("product_type", "slug", "name", "examples", "verdict", "note")


def listing_sheet(
    snapshot: Snapshot,
    *,
    sold: int,
    active: int,
    exclude: Iterable[int] = (),
) -> list[dict[str, str]]:
    """Blind sheet: title, category and designers only, in a stable random order."""

    skip = set(exclude)
    rows: list[ListingView] = []
    for status, size in (("sold", sold), ("active", active)):
        pool = sorted(
            (
                item
                for item in snapshot.listings.values()
                if item.status == status and item.grailed_id not in skip
            ),
            key=lambda item: sample_key(item.grailed_id),
        )
        rows.extend(pool[:size])
    rows.sort(key=lambda item: sample_key(item.grailed_id))
    return [_listing_sheet_row(item) for item in rows]


def _listing_sheet_row(item: ListingView) -> dict[str, str]:
    return {
        "grailed_id": str(item.grailed_id),
        "category_path": item.category_path or "",
        "designers": "; ".join(item.designers),
        "title": item.title,
        **{name: "" for name in LABEL_COLUMNS[1:]},
    }


def phrase_sheet(
    snapshot: Snapshot,
    lines: Sequence[LineStat],
    *,
    exclude: Iterable[tuple[str, str]] = (),
    examples: int = 8,
    seed: int = 0,
) -> list[dict[str, str]]:
    """Blind sheet: the phrase and random sold titles, without rank or counts, shuffled."""

    skip = set(exclude)
    titles: dict[int, list[str]] = defaultdict(list)
    fallback: dict[int, list[str]] = defaultdict(list)
    for listing in sorted(snapshot.listings.values(), key=lambda item: item.grailed_id):
        group = snapshot.groups.get(listing.group_id) if listing.group_id is not None else None
        if group is None:
            continue
        line = snapshot.line_of(group)
        (titles if listing.sold else fallback)[line.id].append(listing.title)
    rng = random.Random(seed)
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in lines:
        if item.key in skip or item.key in seen:
            continue
        seen.add(item.key)
        pool = titles[item.line.id]
        chosen = rng.sample(pool, min(examples, len(pool)))
        if len(chosen) < examples:
            extra = fallback[item.line.id]
            chosen += rng.sample(extra, min(examples - len(chosen), len(extra)))
        rows.append(
            {
                "product_type": item.line.product_type,
                "slug": item.line.slug,
                "name": item.line.name,
                "examples": " || ".join(chosen),
                "verdict": "",
                "note": "",
            }
        )
    rng.shuffle(rows)
    return rows


def write_sheet(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def import_sheet(directory: Path, sheet: Path) -> dict[str, Any]:
    """Merge a filled sheet into labels.csv or phrases.csv; existing rows are never replaced."""

    with sheet.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        rows = list(reader)
    columns: Sequence[str]
    key: tuple[str, ...]
    if "grailed_id" in header:
        target, columns, key = directory / "labels.csv", LABEL_COLUMNS, ("grailed_id",)
        filled = [row for row in rows if (row.get("gold_model") or "").strip()]
        for row in filled:
            parse_gold(row["gold_model"])
    elif "verdict" in header:
        target, columns, key = directory / "phrases.csv", PHRASE_COLUMNS, ("product_type", "slug")
        filled = [row for row in rows if (row.get("verdict") or "").strip()]
        for row in filled:
            parse_verdict(row["verdict"])
    else:
        raise ValueError(f"{sheet.name} is neither a listing nor a phrase sheet")
    existing: list[dict[str, str]] = []
    if target.is_file():
        with target.open(encoding="utf-8", newline="") as handle:
            existing = list(csv.DictReader(handle))
    known = {tuple(row[name] for name in key) for row in existing}
    added = [row for row in filled if tuple(row[name] for name in key) not in known]
    skipped = len(filled) - len(added)
    write_sheet(target, columns, [*existing, *added])
    return {"target": target.name, "added": len(added), "skipped": skipped}


def audit_sheets(
    snapshot: Snapshot,
    lines: Sequence[LineStat],
    labels: Sequence[Label],
    verdicts: Mapping[tuple[str, str], Verdict],
    *,
    listings: int = 120,
    phrases: int = 40,
    phrase_ranks: tuple[int, int] = (51, 150),
    seed: int = 0,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Random labeled rows for a second, blind labeling pass (labels are not shown)."""

    rng = random.Random(seed)
    present = [label for label in labels if label.grailed_id in snapshot.listings]
    chosen = rng.sample(present, min(listings, len(present)))
    listing_rows = [_listing_sheet_row(snapshot.listings[item.grailed_id]) for item in chosen]
    start, end = phrase_ranks
    window = [item for item in lines[start - 1 : end] if item.key in verdicts]
    picked = rng.sample(window, min(phrases, len(window)))
    phrase_rows = phrase_sheet(snapshot, picked, seed=seed)
    return listing_rows, phrase_rows


def audit_agreement(
    directory: Path, listing_sheet_path: Path | None, phrase_sheet_path: Path | None
) -> dict[str, Any]:
    """Agreement of a blind second pass with the committed labels (target: 90%)."""

    result: dict[str, Any] = {}
    canon = load_canon(directory / "canon.csv")
    if listing_sheet_path is not None:
        labels = {label.grailed_id: label for label in load_labels(directory / "labels.csv")}
        hits = total = 0
        disagreements: list[dict[str, str]] = []
        with listing_sheet_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if not (row.get("gold_model") or "").strip():
                    continue
                label = labels.get(int(row["grailed_id"]))
                if label is None:
                    continue
                other = Label(
                    grailed_id=label.grailed_id,
                    gold_type=(row.get("gold_type") or "").strip(),
                    gold=parse_gold(row["gold_model"]),
                    collab=(row.get("gold_collab") or "").strip(),
                    borderline=(row.get("borderline") or "0").strip() == "1",
                )
                total += 1
                if _same_label(label, other, canon):
                    hits += 1
                else:
                    disagreements.append(
                        {
                            "grailed_id": str(label.grailed_id),
                            "committed": _label_text(label),
                            "audit": _label_text(other),
                        }
                    )
        result["listings"] = {"agreement": Share(hits, total), "disagreements": disagreements}
    if phrase_sheet_path is not None:
        verdicts = load_phrases(directory / "phrases.csv")
        hits = total = 0
        phrase_disagreements: list[dict[str, str]] = []
        with phrase_sheet_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if not (row.get("verdict") or "").strip():
                    continue
                key = (row["product_type"], row["slug"])
                if key not in verdicts:
                    continue
                other_verdict = parse_verdict(row["verdict"])
                total += 1
                if other_verdict.kind == verdicts[key].kind:
                    hits += 1
                else:
                    phrase_disagreements.append(
                        {
                            "phrase": f"{key[0]}/{key[1]}",
                            "committed": _verdict_text(verdicts[key]),
                            "audit": _verdict_text(other_verdict),
                        }
                    )
        result["phrases"] = {
            "agreement": Share(hits, total),
            "disagreements": phrase_disagreements,
        }
    return result


def _same_label(left: Label, right: Label, canon: Canon | None = None) -> bool:
    if left.gold.kind != right.gold.kind and not (
        {left.gold.kind, right.gold.kind} <= {"none", "descriptor"}
    ):
        return False
    if left.gold.kind == "model" and not (
        gold_keys(left.gold.line or "", canon) & gold_keys(right.gold.line or "", canon)
    ):
        return False
    return _partner_key(left.collab) == _partner_key(right.collab)


def _label_text(label: Label) -> str:
    gold = label.gold.leaf if label.gold.kind == "model" else label.gold.kind
    return f"{gold} | {label.collab}" if label.collab else str(gold)


# --- Baseline report --------------------------------------------------------------------

BULK_SELLER_LISTINGS = 1000


def baseline_report(
    current: Snapshot,
    scratch: Snapshot | None,
    verdicts: Mapping[tuple[str, str], Verdict],
    config: EvalConfig,
    codesigner_labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Step-0 numbers with their formulas: coverage, history, garbage, sellers, designers."""

    report: dict[str, Any] = {
        "coverage": _coverage(current),
        "groups": _group_counts(current),
        "garbage_extrapolation": _garbage_extrapolation(current, verdicts, config),
        "bulk_sellers": _bulk_sellers(current),
        "codesigners": _codesigners(current, codesigner_labels or {}),
    }
    if scratch is not None:
        report["history"] = _history(current, scratch)
    return report


def _coverage(snapshot: Snapshot) -> dict[str, Any]:
    listings: Counter[str] = Counter()
    sales: Counter[str] = Counter()
    for listing in snapshot.listings.values():
        placement = snapshot.placement(listing.grailed_id)
        assert placement is not None
        if placement.kind in {"none", "review"} or placement.group is None:
            bucket: str = placement.kind
        else:
            bucket = placement.group.status
        listings[bucket] += 1
        sales[bucket] += int(listing.sold)
    total_listings = sum(listings.values())
    total_sales = sum(sales.values())
    return {
        "listings": total_listings,
        "sales": total_sales,
        "by_status": {
            name: {
                "listings": listings[name],
                "listings_share": _ratio(listings[name], total_listings),
                "sales": sales[name],
                "sales_share": _ratio(sales[name], total_sales),
            }
            for name in sorted(listings)
        },
        # "No model" excludes review; the second figure adds it (the 18.5% / 19.5% gap).
        "no_model_sales_share": _ratio(sales["none"], total_sales),
        "no_model_with_review_sales_share": _ratio(sales["none"] + sales["review"], total_sales),
    }


def _group_counts(snapshot: Snapshot) -> dict[str, Any]:
    used: Counter[int] = Counter()
    sold: Counter[int] = Counter()
    for listing in snapshot.listings.values():
        if listing.group_id is not None:
            used[listing.group_id] += 1
            sold[listing.group_id] += int(listing.sold)
    by_status = Counter(f"{group.status}/{group.source}" for group in snapshot.groups.values())
    auto = [group for group in snapshot.groups.values() if group.status == "auto"]
    seeds = [
        group
        for group in snapshot.groups.values()
        if group.status == "confirmed" and group.source == "seed"
    ]
    return {
        "by_status_source": dict(sorted(by_status.items())),
        "auto": len(auto),
        "auto_empty": sum(1 for group in auto if not used[group.id]),
        "auto_without_sales": sum(1 for group in auto if not sold[group.id]),
        "seed_confirmed": len(seeds),
        "seed_confirmed_empty": sum(1 for group in seeds if not used[group.id]),
    }


def _history(current: Snapshot, scratch: Snapshot) -> dict[str, Any]:
    """Which assignments and auto groups depend on past passes rather than on the rules."""

    def key(snapshot: Snapshot, group_id: int | None) -> tuple[str, str] | None:
        group = snapshot.groups.get(group_id) if group_id is not None else None
        return (group.product_type, group.slug) if group else None

    same = 0
    for listing in current.listings.values():
        other = scratch.listings.get(listing.grailed_id)
        if other is not None and key(current, listing.group_id) == key(scratch, other.group_id):
            same += 1
    used = {listing.group_id for listing in current.listings.values()}
    current_auto = {
        (group.product_type, group.slug): group
        for group in current.groups.values()
        if group.status == "auto"
    }
    scratch_auto = {
        (group.product_type, group.slug)
        for group in scratch.groups.values()
        if group.status == "auto"
    }
    only_current = [group for slug, group in current_auto.items() if slug not in scratch_auto]
    return {
        "assignment_agreement": _ratio(same, len(current.listings)),
        "current_auto": len(current_auto),
        "scratch_auto": len(scratch_auto),
        "in_both": len(set(current_auto) & scratch_auto),
        "only_current_empty": sum(1 for group in only_current if group.id not in used),
        "only_current_with_listings": sum(1 for group in only_current if group.id in used),
        "only_scratch": len(scratch_auto - set(current_auto)),
    }


def _garbage_extrapolation(
    snapshot: Snapshot, verdicts: Mapping[tuple[str, str], Verdict], config: EvalConfig
) -> dict[str, Any]:
    """Garbage share of sales = labeled head lines + tail estimate × tail sales."""

    lines = rank_lines(snapshot)
    head = lines[: config.tail_after]
    head_sales = sum(item.sales for item in head)
    tail_sales = sum(item.sales for item in lines[config.tail_after :])

    def head_sum(kind: str) -> int:
        return sum(
            item.sales
            for item in head
            if item.key in verdicts and verdicts[item.key].kind == kind
        )

    head_garbage = head_sum("not_model")
    head_duplicate = head_sum("duplicate")
    tail = tail_metrics(
        tail_sample(lines, after=config.tail_after, size=config.tail_size, seed=config.tail_seed),
        verdicts,
    )
    tail_share = tail["garbage_sales"].point or 0.0
    tail_dup_share = (tail["garbage_dup_sales"].point or 0.0) - tail_share
    model_sales = head_sales + tail_sales
    all_sales = sum(1 for listing in snapshot.listings.values() if listing.sold)
    garbage = round(head_garbage + tail_share * tail_sales)
    duplicate = round(head_duplicate + tail_dup_share * tail_sales)
    return {
        "formula": "(head garbage sales + tail garbage share × tail sales) / sales",
        "head_lines": len(head),
        "head_sales": head_sales,
        "head_garbage_sales": head_garbage,
        "head_duplicate_sales": head_duplicate,
        "head_unlabeled_sales": sum(item.sales for item in head if item.key not in verdicts),
        "tail_sales": tail_sales,
        "tail_garbage_share": tail["garbage_sales"],
        "tail_duplicate_share": round(tail_dup_share, 4),
        "model_sales": model_sales,
        "all_sales": all_sales,
        "garbage_share_of_model_sales": _ratio(garbage, model_sales),
        "garbage_share_of_all_sales": _ratio(garbage, all_sales),
        "duplicate_share_of_all_sales": _ratio(duplicate, all_sales),
    }


def _bulk_sellers(snapshot: Snapshot) -> dict[str, Any]:
    """Aggregates only: seller identities never leave this function."""

    listings: Counter[str] = Counter()
    sales: Counter[str] = Counter()
    for listing in snapshot.listings.values():
        if listing.seller:
            listings[listing.seller] += 1
            sales[listing.seller] += int(listing.sold)
    bulk = {seller for seller, count in listings.items() if count >= BULK_SELLER_LISTINGS}
    bulk_listings = sum(listings[seller] for seller in bulk)
    bulk_sales = sum(sales[seller] for seller in bulk)
    total_listings = sum(listings.values())
    total_sales = sum(sales.values())
    return {
        "threshold_listings": BULK_SELLER_LISTINGS,
        "sellers": len(bulk),
        "listings_share": _ratio(bulk_listings, total_listings),
        "sales_share": _ratio(bulk_sales, total_sales),
        "sell_through_bulk": _ratio(bulk_sales, bulk_listings),
        "sell_through_others": _ratio(total_sales - bulk_sales, total_listings - bulk_listings),
    }


def _codesigners(snapshot: Snapshot, labels: Mapping[str, str]) -> list[dict[str, Any]]:
    """Every co-designer of the brand's listings with its manual class (codesigners.csv)."""

    listings: Counter[str] = Counter()
    sales: Counter[str] = Counter()
    brand = fold(snapshot.brand.replace("-", " "))
    for listing in snapshot.listings.values():
        for name in set(listing.designers):
            if fold(name) == brand:
                continue
            listings[name] += 1
            sales[name] += int(listing.sold)
    return [
        {
            "designer": name,
            "listings": count,
            "sales": sales[name],
            "class": labels.get(fold(name), "unlabeled"),
        }
        for name, count in sorted(listings.items(), key=lambda item: (-sales[item[0]], -item[1]))
    ]


def load_codesigners(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {fold(row["designer"]): row["class"].strip() for row in csv.DictReader(handle)}
