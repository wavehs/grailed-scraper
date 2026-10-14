"""Descriptions, collaborations and spellings in numbers: what they cut off, clash with,
miss or may duplicate."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from rapidfuzz.distance import Levenshtein
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand, Listing, ListingModelAssignment, ModelBlock, ModelGroup
from app.services.grouping.brands import brand_normalizer, load_brand_terms
from app.services.grouping.collabs import COLLAB_PREFIX
from app.services.grouping.descriptors import DESCRIPTOR_PREFIX
from app.services.grouping.kinds import NONE_SLUG, group_kind, is_service_slug
from app.services.grouping.mining import MAX_WORDS, MiningSample, covered, mine_phrases
from app.services.grouping.normalize import TitleNormalizer
from app.services.grouping.policy import REVIEW_TYPE, GroupingPolicy
from app.services.grouping.text import fold


def seed_conflicts(policy: GroupingPolicy, brand_slug: str) -> list[dict[str, Any]]:
    """Seed names made of descriptions, and the types where that happens.

    A seed is always found first, so nothing is lost; the list shows where a model and a
    description share words ("paris" for the sneaker Paris, "cities" against the bag City).
    """

    normalizer = TitleNormalizer(
        policy, brand_terms=[brand_slug.replace("-", " ")], brand_slug=brand_slug
    )
    index = policy.descriptor_index(brand_slug)
    rows: list[dict[str, Any]] = []
    for seed in policy.seed_models(brand_slug):
        types = seed.types or tuple(policy.taxonomy.types)
        for alias in seed.aliases:
            tokens = normalizer.phrase(alias)
            cut = [item for item in types if covered(tokens, frozenset(), index.phrases(item))]
            if tokens and cut:
                rows.append(
                    {
                        "seed": seed.name,
                        "alias": alias,
                        "seed_types": list(seed.types) if seed.types else "any",
                        "described_in": cut,
                    }
                )
    return rows


async def descriptor_report(
    session: AsyncSession, policy: GroupingPolicy, brand: Brand, *, limit: int = 30
) -> dict[str, Any]:
    """Groups of descriptions, phrases they cut off from mining, and clashes with seeds."""

    index = policy.descriptor_index(brand.slug)
    classes = {descriptor.slug: descriptor.klass for descriptor in index.descriptors}
    sold = func.sum(func.iif(Listing.status == "sold", 1, 0))
    groups = (
        await session.execute(
            select(ModelGroup.product_type, ModelGroup.slug, ModelGroup.name, func.count(), sold)
            .join(ListingModelAssignment, ListingModelAssignment.model_group_id == ModelGroup.id)
            .join(Listing, Listing.id == ListingModelAssignment.listing_id)
            .where(
                ModelGroup.brand_id == brand.id, ModelGroup.slug.startswith(DESCRIPTOR_PREFIX)
            )
            .group_by(ModelGroup.id)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return {
        "descriptor_groups": [
            {
                "product_type": row[0],
                "name": row[2],
                "class": classes.get(row[1]),
                "listings": row[3],
                "sales": int(row[4] or 0),
            }
            for row in groups
        ],
        "suppressed_candidates": await suppressed_candidates(session, policy, brand, limit=limit),
        "seed_conflicts": seed_conflicts(policy, brand.slug or ""),
    }


async def suppressed_candidates(
    session: AsyncSession, policy: GroupingPolicy, brand: Brand, *, limit: int = 30
) -> list[dict[str, Any]]:
    """Phrases mining would take as models without the descriptions, with their sales.

    The population is what mining sees: listings in "No model", in a description group or in
    a mined group. Read-only; it reflects the stored assignments.
    """

    normalizer = brand_normalizer(policy, brand, await load_brand_terms(session, brand))
    index = policy.descriptor_index(brand.slug)
    groups = list(await session.scalars(select(ModelGroup).where(ModelGroup.brand_id == brand.id)))
    blocked = [
        tokens
        for value in await session.scalars(
            select(ModelBlock.phrase).where(ModelBlock.brand_id == brand.id)
        )
        if (tokens := normalizer.phrase(value))
    ]
    population = or_(
        ModelGroup.slug == NONE_SLUG,
        ModelGroup.slug.startswith(DESCRIPTOR_PREFIX),
        ModelGroup.slug.startswith(COLLAB_PREFIX),
        and_(ModelGroup.status == "auto", ModelGroup.source == "mined"),
    )
    samples: dict[str, list[tuple[MiningSample, bool]]] = defaultdict(list)
    for title, product_type, seller, status, listing_id in await session.execute(
        select(
            Listing.title,
            Listing.product_type,
            Listing.seller_identity,
            Listing.status,
            Listing.id,
        )
        .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
        .join(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
        .where(Listing.brand_id == brand.id, Listing.product_type != REVIEW_TYPE, population)
    ):
        normalized = normalizer.normalize(title)
        sample = MiningSample(
            seller or f"listing:{listing_id}", normalized.tokens, normalized.surfaces
        )
        samples[product_type].append((sample, status == "sold"))
    found: list[dict[str, Any]] = []
    for product_type, items in samples.items():
        known = [
            group
            for group in groups
            if group.product_type == product_type
            and not (group.status == "auto" and group.source == "mined")
            and not group.slug.startswith((DESCRIPTOR_PREFIX, COLLAB_PREFIX))
        ]
        excluded = {
            tokens
            for group in known
            for value in (group.name, *group.aliases)
            if (tokens := normalizer.phrase(value))
        }
        excluded |= {tuple(group.slug.split("-")) for group in known}
        plain = [sample for sample, _ in items]
        without = mine_phrases(
            plain, excluded=excluded, generic=policy.words.generic, generic_phrases=blocked
        )
        kept = {
            item.tokens
            for item in mine_phrases(
                plain,
                excluded=excluded,
                generic=policy.words.generic,
                generic_phrases=(*blocked, *index.phrases(product_type)),
            )
        }
        cut = {item.tokens: item for item in without if item.tokens not in kept}
        for tokens, listings, sales in _count(items, set(cut)):
            found.append(
                {
                    "product_type": product_type,
                    "phrase": cut[tokens].name,
                    "sellers": cut[tokens].sellers,
                    "listings": listings,
                    "sales": sales,
                }
            )
    found.sort(key=lambda row: (-row["sales"], -row["listings"], row["phrase"]))
    return found[:limit]


async def collab_report(
    session: AsyncSession, policy: GroupingPolicy, brand: Brand, *, examples: int = 5
) -> dict[str, Any]:
    """Listings whose Grailed designers name a whitelisted collaboration, but which ended up
    neither in its line nor in a model: the title lacks the partner's name or alias.

    ``designers`` are a hint here only (they never place a listing). Read-only.
    """

    collabs = policy.brand_collabs(brand.slug)
    if not collabs:
        return {"collab_hints": []}
    totals: Counter[str] = Counter()
    missed: Counter[str] = Counter()
    sales: Counter[str] = Counter()
    kinds: dict[str, Counter[str]] = defaultdict(Counter)
    titles: dict[str, list[str]] = defaultdict(list)
    rows = await session.execute(
        select(
            Listing.title,
            Listing.designer_names,
            Listing.status,
            ModelGroup.slug,
            ModelGroup.product_type,
        )
        .outerjoin(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
        .outerjoin(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
        .where(Listing.brand_id == brand.id)
        .order_by(Listing.id)
    )
    for title, designers, status, slug, product_type in rows:
        names = {fold(str(name)).strip() for name in designers or ()}
        kind = group_kind(slug, product_type) if slug is not None else "none"
        for collab in collabs:
            if not names & collab.designers:
                continue
            totals[collab.name] += 1
            if kind == "model" or slug == collab.slug:
                continue
            missed[collab.name] += 1
            sales[collab.name] += int(status == "sold")
            kinds[collab.name][kind] += 1
            if len(titles[collab.name]) < examples:
                titles[collab.name].append(title)
    return {
        "collab_hints": [
            {
                "collab": collab.name,
                "slug": collab.slug,
                "with_designer": totals[collab.name],
                "unplaced": missed[collab.name],
                "unplaced_sales": sales[collab.name],
                "by_kind": dict(kinds[collab.name]),
                "examples": titles[collab.name],
            }
            for collab in collabs
            if totals[collab.name]
        ]
    }


# Names shorter than this differ by one letter too often to mean the same model ("Cash"/"Case").
MERGE_MIN_LENGTH = 5


@dataclass(frozen=True, slots=True)
class LineRow:
    """A model group of one product type with its listings and sales."""

    id: int
    product_type: str
    slug: str
    name: str
    status: str
    parent_id: int | None
    listings: int
    sales: int


def merge_pairs(lines: Sequence[LineRow]) -> list[dict[str, Any]]:
    """Groups of one type that may be one model: a typo apart or written as one word.

    Nothing is merged automatically ("Strike" and "Striker" are both real words); a person
    merges in the interface. Sorted by the sales the pair holds together.
    """

    by_type: dict[str, list[LineRow]] = defaultdict(list)
    for line in lines:
        by_type[line.product_type].append(line)
    pairs: list[dict[str, Any]] = []
    for product_type, items in sorted(by_type.items()):
        items.sort(key=lambda item: item.slug)
        for left, right in combinations(items, 2):
            if left.parent_id == right.id or right.parent_id == left.id:
                continue
            reason = _merge_reason(left.slug, right.slug)
            if reason is None:
                continue
            pairs.append(
                {
                    "product_type": product_type,
                    "reason": reason,
                    "sales": left.sales + right.sales,
                    "groups": [_line_ref(item) for item in (left, right)],
                }
            )
    pairs.sort(key=lambda pair: (-pair["sales"], pair["product_type"], pair["groups"][0]["slug"]))
    return pairs


async def merge_candidates(
    session: AsyncSession, brand: Brand, *, limit: int = 50
) -> dict[str, Any]:
    """``merge_pairs`` over the brand's live model groups (no service groups, no tombstones)."""

    lines = [line for line in await _live_lines(session, brand) if line.listings]
    return {"merge_candidates": merge_pairs(lines)[:limit]}


def candidate_lines(lines: Sequence[LineRow], *, limit: int = 50) -> list[dict[str, Any]]:
    """Mined lines ranked by the sales of the line and its versions, with the groups they may
    be one model with. Only lines: a version follows its line into the seed file."""

    versions: dict[int, list[LineRow]] = defaultdict(list)
    for line in lines:
        if line.parent_id is not None:
            versions[line.parent_id].append(line)
    partners: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for pair in merge_pairs([line for line in lines if line.listings]):
        left, right = pair["groups"]
        partners[left["id"]].append({**right, "reason": pair["reason"]})
        partners[right["id"]].append({**left, "reason": pair["reason"]})
    rows: list[dict[str, Any]] = [
        {
            "id": line.id,
            "product_type": line.product_type,
            "name": line.name,
            "slug": line.slug,
            "listings": line.listings + sum(item.listings for item in versions[line.id]),
            "sales": line.sales + sum(item.sales for item in versions[line.id]),
            "versions": sorted(item.name for item in versions[line.id]),
            "merge_with": partners[line.id],
            "seed": f"{{name: {line.name}, types: [{line.product_type}]}}",
        }
        for line in lines
        if line.status == "auto" and line.parent_id is None
    ]
    rows.sort(key=lambda row: (-row["sales"], -row["listings"], row["product_type"], row["slug"]))
    return rows[:limit]


async def model_candidates(
    session: AsyncSession, brand: Brand, *, limit: int = 50, examples: int = 8
) -> dict[str, Any]:
    """Top mined lines with example titles: the list a person marks to confirm models.

    A marked line goes into ``config/models/<brand>.yaml``; the seed then takes the group
    with its id (docs/GROUPING.md §6). Sold titles come first, newest first. Read-only.
    """

    rows = candidate_lines(await _live_lines(session, brand), limit=limit)
    for row in rows:
        members = [row["id"], *(
            await session.scalars(select(ModelGroup.id).where(ModelGroup.parent_id == row["id"]))
        )]
        titles = await session.scalars(
            select(Listing.title)
            .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .where(ListingModelAssignment.model_group_id.in_(members))
            .order_by((Listing.status == "sold").desc(), Listing.created_at.desc(), Listing.id)
            .limit(examples * 5)
        )
        row["titles"] = list(dict.fromkeys(titles))[:examples]
    return {"model_candidates": rows}


async def _live_lines(session: AsyncSession, brand: Brand) -> list[LineRow]:
    """The brand's live model groups with their listings and sales (no service groups);
    a line whose listings all sit in its versions is here with zero."""

    sold = func.sum(func.iif(Listing.status == "sold", 1, 0))
    rows = (
        await session.execute(
            select(
                ModelGroup.id,
                ModelGroup.product_type,
                ModelGroup.slug,
                ModelGroup.name,
                ModelGroup.status,
                ModelGroup.parent_id,
                func.count(Listing.id),
                sold,
            )
            .outerjoin(
                ListingModelAssignment, ListingModelAssignment.model_group_id == ModelGroup.id
            )
            .outerjoin(Listing, Listing.id == ListingModelAssignment.listing_id)
            .where(
                ModelGroup.brand_id == brand.id,
                ModelGroup.status != "ignored",
                ModelGroup.retired_at.is_(None),
                ModelGroup.product_type != REVIEW_TYPE,
            )
            .group_by(ModelGroup.id)
        )
    ).all()
    return [
        LineRow(row[0], row[1], row[2], row[3], row[4], row[5], row[6], int(row[7] or 0))
        for row in rows
        if not is_service_slug(row[2])
    ]


def _merge_reason(left: str, right: str) -> str | None:
    left_words, right_words = left.replace("-", " "), right.replace("-", " ")
    if left_words.replace(" ", "") == right_words.replace(" ", ""):
        return "spelling"
    if min(len(left_words), len(right_words)) < MERGE_MIN_LENGTH:
        return None
    if abs(len(left_words) - len(right_words)) > 1:
        return None
    if Levenshtein.distance(left_words, right_words, score_cutoff=1) > 1:
        return None
    # "Track 2" and "Track 3" are two versions, "Sporty 2" is no typo of "Sporty B".
    for op in Levenshtein.editops(left_words, right_words):
        removed = left_words[op.src_pos] if op.tag != "insert" else ""
        added = right_words[op.dest_pos] if op.tag != "delete" else ""
        if removed.isdigit() or added.isdigit():
            return None
    return "typo"


def _line_ref(line: LineRow) -> dict[str, Any]:
    return {
        "id": line.id,
        "slug": line.slug,
        "name": line.name,
        "status": line.status,
        "listings": line.listings,
        "sales": line.sales,
    }


def _count(
    items: list[tuple[MiningSample, bool]], targets: set[tuple[str, ...]]
) -> list[tuple[tuple[str, ...], int, int]]:
    listings: dict[tuple[str, ...], int] = defaultdict(int)
    sales: dict[tuple[str, ...], int] = defaultdict(int)
    for sample, sold in items:
        tokens = sample.tokens
        seen = {
            tokens[start : start + size]
            for size in range(1, MAX_WORDS + 1)
            for start in range(len(tokens) - size + 1)
            if tokens[start : start + size] in targets
        }
        for gram in seen:
            listings[gram] += 1
            sales[gram] += int(sold)
    return [(gram, listings[gram], sales[gram]) for gram in targets if listings[gram]]
