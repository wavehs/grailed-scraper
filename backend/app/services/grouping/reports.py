"""Descriptions and collaborations in numbers: what they cut off, clash with or miss."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand, Listing, ListingModelAssignment, ModelBlock, ModelGroup
from app.services.grouping.brands import brand_normalizer, load_brand_terms
from app.services.grouping.collabs import COLLAB_PREFIX
from app.services.grouping.descriptors import DESCRIPTOR_PREFIX
from app.services.grouping.kinds import NONE_SLUG, group_kind
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
