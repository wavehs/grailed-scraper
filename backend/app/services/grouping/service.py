"""Regroup a brand's listings: product type, model line/version, mined models and relists."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Table, bindparam, delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Brand,
    GroupMetric,
    Listing,
    ListingModelAssignment,
    ListingOverride,
    ModelBlock,
    ModelGroup,
    ParentOverride,
)
from app.domain.listings import slugify
from app.services.grouping.assign import (
    ModelDictionary,
    ModelEntry,
    TypeDecision,
    classify_type,
)
from app.services.grouping.brands import brand_normalizer, load_brand_terms
from app.services.grouping.collabs import Collab, CollabIndex
from app.services.grouping.descriptors import Descriptor, DescriptorIndex
from app.services.grouping.kinds import NONE_SLUG, is_collab_slug, is_descriptor_slug
from app.services.grouping.mining import (
    MIN_SELLERS,
    MiningSample,
    covered,
    mine_phrases,
    parent_line,
)
from app.services.grouping.normalize import NormalizedTitle, TitleNormalizer
from app.services.grouping.policy import REVIEW_TYPE, GroupingPolicy, SeedModel, load_policy
from app.services.grouping.relists import RelistRow, detect_relists

GROUPING_VERSION = "grouping-v7"
# Raised when a code change moves listings or lines: the rules hash changes, so the next pass
# is a full one. 4: a mined version hangs only under the line it starts with.
_RULES_REVISION = 4
NONE_NAME = "No model"
_WRITE_CHUNK = 500
# Listings without a model group: they feed mining and are looked at again when a group appears.
_NO_MODEL = frozenset({"none", "descriptor", "collab"})


@dataclass(slots=True)
class BrandGroupingStats:
    brand_id: int
    listings: int = 0
    processed: int = 0
    changed: int = 0
    with_model: int = 0
    no_model: int = 0
    descriptors: int = 0
    collabs: int = 0
    review: int = 0
    groups_created: int = 0
    mined: int = 0
    relists: int = 0
    retired: int = 0
    revived: int = 0
    deleted: int = 0
    inherited: int = 0
    promoted: int = 0
    full: bool = True
    unknown_categories: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "brand_id": self.brand_id,
            "listings": self.listings,
            "processed": self.processed,
            "changed": self.changed,
            "with_model": self.with_model,
            "no_model": self.no_model,
            "descriptors": self.descriptors,
            "collabs": self.collabs,
            "review": self.review,
            "groups_created": self.groups_created,
            "mined": self.mined,
            "relists": self.relists,
            "retired": self.retired,
            "revived": self.revived,
            "deleted": self.deleted,
            "inherited": self.inherited,
            "promoted": self.promoted,
            "full": self.full,
            "unknown_categories": dict(self.unknown_categories),
        }


@dataclass(slots=True)
class GroupingResult:
    brands: list[BrandGroupingStats]
    duration_s: float

    def summary(self) -> dict[str, Any]:
        totals = Counter[str]()
        unknown = Counter[str]()
        for item in self.brands:
            for key in (
                "listings",
                "changed",
                "with_model",
                "no_model",
                "descriptors",
                "collabs",
                "review",
                "mined",
            ):
                totals[key] += int(getattr(item, key))
            totals["groups_created"] += item.groups_created
            totals["relists"] += item.relists
            for key in ("retired", "revived", "deleted", "inherited", "promoted"):
                totals[key] += int(getattr(item, key))
            unknown.update(item.unknown_categories)
        return {
            "version": GROUPING_VERSION,
            "brands": len(self.brands),
            **dict(totals),
            "unknown_categories": dict(unknown.most_common(20)),
            "duration_s": round(self.duration_s, 2),
        }


@dataclass(slots=True)
class _Row:
    id: int
    grailed_id: int
    title: str
    category_path: str | None
    designers: tuple[str, ...]
    seller: str | None
    status: str
    created_at: datetime
    last_seen_at: datetime
    repost_of: int | None
    product_type: str | None
    relist_of_id: int | None
    group_id: int | None
    method: str | None
    input_hash: str | None
    override: int | None
    normalized: NormalizedTitle | None = None
    new_hash: str = ""


@dataclass(slots=True)
class _Decision:
    product_type: str
    group_id: int
    method: str


class GroupingService:
    """Deterministic grouping; manual rules live in the database and survive regrouping."""

    def __init__(
        self,
        session: AsyncSession,
        policy: GroupingPolicy | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._policy = policy or load_policy()
        self._now = clock or (lambda: datetime.now(UTC))

    async def regroup(
        self, brand_ids: Iterable[int] | None = None, *, full: bool = False
    ) -> GroupingResult:
        started = time.perf_counter()
        if brand_ids is None:
            selected = list(await self._session.scalars(select(Brand.id).order_by(Brand.id)))
        else:
            selected = sorted(set(brand_ids))
        stats = [await self.regroup_brand(brand_id, full=full) for brand_id in selected]
        return GroupingResult(stats, time.perf_counter() - started)

    async def regroup_brand(self, brand_id: int, *, full: bool = False) -> BrandGroupingStats:
        brand = await self._session.get(Brand, brand_id)
        stats = BrandGroupingStats(brand_id)
        if brand is None:
            return stats
        now = self._now()
        rows = await self._rows(brand_id)
        stats.listings = len(rows)
        designers = sorted({name for row in rows for name in row.designers})
        terms = await load_brand_terms(self._session, brand, designers)
        blocked = sorted(
            await self._session.scalars(
                select(ModelBlock.phrase).where(ModelBlock.brand_id == brand_id)
            )
        )
        normalizer = brand_normalizer(self._policy, brand, terms)
        descriptors = self._policy.descriptor_index(brand.slug)
        collabs = CollabIndex(self._policy.brand_collabs(brand.slug), normalizer.spelled)
        groups = {
            group.id: group
            for group in await self._session.scalars(
                select(ModelGroup).where(ModelGroup.brand_id == brand_id)
            )
        }
        # Retired groups that end empty again keep their date, so the TTL keeps running.
        was_retired = {
            group.id: group.retired_at
            for group in groups.values()
            if group.retired_at is not None
        }
        parents = {
            override.group_id: override.parent_id
            for override in await self._session.scalars(
                select(ParentOverride)
                .join(ModelGroup, ModelGroup.id == ParentOverride.group_id)
                .where(ModelGroup.brand_id == brand_id)
            )
        }
        seeds = self._policy.seed_models(brand.slug)
        stats.groups_created += await self._seed(brand, seeds, groups, normalizer, None, stats)
        _apply_parents(groups, parents)

        cache: dict[str, NormalizedTitle] = {}
        for row in rows:
            if row.title not in cache:
                cache[row.title] = normalizer.normalize(row.title)
            row.normalized = cache[row.title]
            row.new_hash = _input_hash(row)

        rules_hash = self._rules_hash(groups, rows, blocked, terms, parents)
        incremental = not full and brand.grouping_hash == rules_hash
        stats.full = not incremental
        protected = _protected(groups, rows, parents)
        weights = Counter(row.group_id for row in rows if row.group_id is not None)

        def first(group: ModelGroup) -> bool:
            # A full pass derives mined groups again; a delta pass only adds to them.
            return incremental or not _derived(group) or group.id in protected

        dictionary = self._dictionary(groups, normalizer, weights, first)
        taxonomy = self._policy.taxonomy

        decisions: dict[int, TypeDecision] = {}
        for row in rows:
            assert row.normalized is not None
            decision = classify_type(taxonomy, row.category_path, row.normalized, dictionary)
            decisions[row.id] = decision
            if not decision.known_category:
                key = row.category_path or "—"
                stats.unknown_categories[key] = stats.unknown_categories.get(key, 0) + 1
        present = {decision.product_type for decision in decisions.values()} - {REVIEW_TYPE}
        created = await self._seed(brand, seeds, groups, normalizer, present, stats)
        stats.groups_created += created
        if created or stats.promoted:
            # New seed groups change the dictionary: the pass becomes a full one, and `first`
            # must already see it as full when the dictionary is rebuilt.
            incremental = False
            stats.full = True
            _apply_parents(groups, parents)
            dictionary = self._dictionary(groups, normalizer, weights, first)

        targets = [
            row
            for row in rows
            if not incremental
            or row.group_id is None
            or row.input_hash != row.new_hash
            or row.override is not None
        ]
        stats.processed = len(targets)
        assigned = await self._assign(brand, targets, decisions, dictionary, groups, normalizer)
        stats.groups_created += assigned[1]
        results: dict[int, _Decision] = assigned[0]
        for row in rows:
            if row.id not in results and row.group_id is not None and row.product_type:
                results[row.id] = _Decision(row.product_type, row.group_id, row.method or "none")

        generic_phrases = tuple(
            tokens for phrase in blocked if (tokens := normalizer.phrase(phrase))
        )
        derived: set[int] = set()
        fresh: set[int] = set()
        if not incremental or targets:
            # A delta pass only adds phrases that new or changed listings bring in.
            derived, fresh = await self._mine(
                brand,
                rows,
                results,
                groups,
                normalizer,
                protected=protected,
                full=not incremental,
                generic_phrases=generic_phrases,
                descriptors=descriptors,
                scope=None if not incremental else {row.id for row in targets},
            )
        stats.mined = len(derived)
        stats.groups_created += len(fresh)
        if derived:
            _apply_parents(groups, parents)
            dictionary = self._dictionary(
                groups, normalizer, weights, lambda group: first(group) or group.id in derived
            )
            retry = [row for row in rows if results[row.id].method in _NO_MODEL]
            again, extra = await self._assign(
                brand, retry, decisions, dictionary, groups, normalizer
            )
            stats.groups_created += extra
            results.update(again)
        # Order of placement: model (above), then collaboration, then description, then "No model".
        stats.groups_created += await self._collaborate(brand, rows, results, groups, collabs)
        stats.groups_created += await self._describe(brand, rows, results, groups, descriptors)
        if not incremental:
            generic_words = self._policy.words.generic
            blocked_phrases = frozenset(generic_phrases)

            def forbidden(product_type: str, tokens: Sequence[str]) -> bool:
                return covered(
                    tokens, generic_words, blocked_phrases | descriptors.phrases(product_type)
                )

            stats.inherited = await self._inherit(
                rows, results, groups, fresh, protected, normalizer, forbidden
            )
        await self._settle(
            groups, results, protected, now, stats, was_retired, full=not incremental, fresh=fresh
        )

        relists = detect_relists(
            [
                RelistRow(
                    id=row.id,
                    grailed_id=row.grailed_id,
                    seller=row.seller,
                    group_id=results[row.id].group_id,
                    status=row.status,
                    created_at=row.created_at,
                    last_seen_at=row.last_seen_at,
                    repost_of=row.repost_of,
                )
                for row in rows
            ]
        )
        stats.relists = len(relists)
        stats.changed = await self._write(rows, results, relists)
        for row in rows:
            method = results[row.id].method
            if method == "review":
                stats.review += 1
            elif method == "none":
                stats.no_model += 1
            elif method == "descriptor":
                stats.descriptors += 1
            elif method == "collab":
                stats.collabs += 1
            else:
                stats.with_model += 1
        brand.grouping_hash = self._rules_hash(groups, rows, blocked, terms, parents)
        brand.grouped_at = now
        await self._session.flush()
        return stats

    async def _rows(self, brand_id: int) -> list[_Row]:
        statement = (
            select(
                Listing.id,
                Listing.grailed_id,
                Listing.title,
                Listing.category_path,
                Listing.designer_names,
                Listing.seller_identity,
                Listing.status,
                Listing.created_at,
                Listing.first_seen_at,
                Listing.last_seen_at,
                Listing.source_repost_id,
                Listing.product_type,
                Listing.relist_of_id,
                ListingModelAssignment.model_group_id,
                ListingModelAssignment.method,
                ListingModelAssignment.input_hash,
                ListingOverride.model_group_id,
            )
            .outerjoin(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .outerjoin(ListingOverride, ListingOverride.listing_id == Listing.id)
            .where(Listing.brand_id == brand_id)
            .order_by(Listing.id)
        )
        rows: list[_Row] = []
        for item in await self._session.execute(statement):
            created = _aware(item[7] or item[8])
            rows.append(
                _Row(
                    id=item[0],
                    grailed_id=item[1],
                    title=item[2],
                    category_path=item[3],
                    designers=tuple(item[4] or ()),
                    seller=item[5],
                    status=item[6],
                    created_at=created,
                    last_seen_at=_aware(item[9]),
                    repost_of=item[10],
                    product_type=item[11],
                    relist_of_id=item[12],
                    group_id=item[13],
                    method=item[14],
                    input_hash=item[15],
                    override=item[16],
                )
            )
        return rows

    async def _seed(
        self,
        brand: Brand,
        seeds: Sequence[SeedModel],
        groups: dict[int, ModelGroup],
        normalizer: TitleNormalizer,
        present: set[str] | None,
        stats: BrandGroupingStats,
    ) -> int:
        """Create missing seed models; ``present`` limits "any" seeds to types in use.

        A mined group with the seed phrase becomes the seed and keeps its id: this is how a
        confirmed model moves from the review list into ``config/models``.
        """

        existing = {(group.product_type, group.slug): group for group in groups.values()}
        created: list[tuple[ModelGroup, str | None]] = []
        promoted: list[tuple[ModelGroup, str | None]] = []
        now = self._now()
        for seed in seeds:
            if seed.types is None:
                if present is None:
                    continue
                types = sorted(present)
            elif present is not None:
                continue
            else:
                types = list(seed.types)
            slug = _slug(normalizer.phrase(seed.name), seed.name)
            for product_type in types:
                found = existing.get((product_type, slug))
                if found is not None:
                    if _derived(found):
                        found.status, found.source = "confirmed", "seed"
                        found.aliases = _merged(
                            [*seed.aliases[1:], found.name, *found.aliases], seed.name
                        )
                        found.name = seed.name
                        found.infer = seed.infer == product_type
                        # The seed file owns the hierarchy: a line the miner once put above
                        # the group must not stay protected as its parent.
                        found.parent_id = None
                        found.retired_at = None
                        found.updated_at = now
                        promoted.append((found, seed.parent))
                    continue
                group = ModelGroup(
                    brand_id=brand.id,
                    product_type=product_type,
                    slug=slug,
                    name=seed.name,
                    aliases=list(seed.aliases[1:]),
                    status="confirmed",
                    source="seed",
                    infer=seed.infer == product_type,
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(group)
                existing[(product_type, slug)] = group
                created.append((group, seed.parent))
        stats.promoted += len(promoted)
        if not created and not promoted:
            return 0
        await self._session.flush()
        for group, _ in created:
            groups[group.id] = group
        by_name = {
            (group.product_type, group.name.casefold()): group for group in groups.values()
        }
        for group, parent_name in [*created, *promoted]:
            if parent_name:
                parent = by_name.get((group.product_type, parent_name.casefold()))
                if parent is not None and parent.id != group.id and parent.parent_id is None:
                    group.parent_id = parent.id
        await self._session.flush()
        return len(created)

    def _dictionary(
        self,
        groups: dict[int, ModelGroup],
        normalizer: TitleNormalizer,
        weights: Counter[int],
        include: Callable[[ModelGroup], bool],
    ) -> ModelDictionary:
        entries = []
        for group in groups.values():
            if (
                group.status == "ignored"
                or group.slug == NONE_SLUG
                or is_descriptor_slug(group.slug)
                or is_collab_slug(group.slug)
                or group.retired_at is not None
                or not include(group)
            ):
                continue
            phrases = tuple(
                dict.fromkeys(
                    tokens
                    for value in (group.name, *group.aliases)
                    if (tokens := normalizer.phrase(value))
                )
            )
            if not phrases:
                continue
            entries.append(
                ModelEntry(
                    key=group.id,
                    product_type=group.product_type,
                    name=group.name,
                    phrases=phrases,
                    parent=group.parent_id,
                    status=group.status,
                    infer=group.infer,
                    weight=weights.get(group.id, 0),
                )
            )
        words = self._policy.words
        return ModelDictionary(
            entries,
            protected_modifiers=words.protected_modifiers,
            version_numbers=words.version_numbers,
        )

    async def _assign(
        self,
        brand: Brand,
        rows: Sequence[_Row],
        decisions: dict[int, TypeDecision],
        dictionary: ModelDictionary,
        groups: dict[int, ModelGroup],
        normalizer: TitleNormalizer,
    ) -> tuple[dict[int, _Decision], int]:
        results: dict[int, _Decision] = {}
        versions: dict[tuple[str, tuple[str, ...]], list[int]] = defaultdict(list)
        version_parents: dict[tuple[str, tuple[str, ...]], ModelGroup] = {}
        none_needed: dict[int, str] = {}
        for row in rows:
            if row.override is not None and row.override in groups:
                target = groups[row.override]
                results[row.id] = _Decision(target.product_type, target.id, "override")
                continue
            product_type = decisions[row.id].product_type
            assert row.normalized is not None
            if product_type == REVIEW_TYPE:
                none_needed[row.id] = REVIEW_TYPE
                continue
            match = dictionary.match(product_type, row.normalized.tokens)
            if match is None:
                none_needed[row.id] = product_type
                continue
            line = groups[match.entry.key]
            if match.version is not None:
                existing = dictionary.find(product_type, match.version)
                if existing is not None:
                    results[row.id] = _Decision(product_type, existing.key, "version")
                else:
                    versions[(product_type, match.version)].append(row.id)
                    version_parents[(product_type, match.version)] = line
                continue
            method = "fuzzy" if match.fuzzy else "phrase"
            results[row.id] = _Decision(product_type, line.id, method)
        created = 0
        now = self._now()
        by_slug = {(group.product_type, group.slug): group for group in groups.values()}
        for (product_type, tokens), listing_ids in versions.items():
            slug = _slug(tokens, " ".join(tokens))
            group = by_slug.get((product_type, slug))
            if group is None or group.status == "ignored":
                if group is not None:
                    for listing_id in listing_ids:
                        none_needed[listing_id] = product_type
                    continue
                parent = version_parents[(product_type, tokens)]
                group = ModelGroup(
                    brand_id=brand.id,
                    product_type=product_type,
                    slug=slug,
                    name=_version_name(tokens, parent, normalizer),
                    aliases=[" ".join(tokens)],
                    parent_id=parent.id,
                    status="auto",
                    source="mined",
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(group)
                await self._session.flush()
                groups[group.id] = group
                by_slug[(product_type, slug)] = group
                created += 1
            elif group.retired_at is not None:
                group.retired_at = None
            for listing_id in listing_ids:
                results[listing_id] = _Decision(product_type, group.id, "version")
        for listing_id, product_type in none_needed.items():
            bucket = by_slug.get((product_type, NONE_SLUG))
            if bucket is None:
                bucket = ModelGroup(
                    brand_id=brand.id,
                    product_type=product_type,
                    slug=NONE_SLUG,
                    name=NONE_NAME,
                    aliases=[],
                    status="confirmed",
                    source="system",
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(bucket)
                await self._session.flush()
                groups[bucket.id] = bucket
                by_slug[(product_type, NONE_SLUG)] = bucket
                created += 1
            method = "review" if product_type == REVIEW_TYPE else "none"
            results[listing_id] = _Decision(product_type, bucket.id, method)
        return results, created

    async def _describe(
        self,
        brand: Brand,
        rows: Sequence[_Row],
        results: dict[int, _Decision],
        groups: dict[int, ModelGroup],
        descriptors: DescriptorIndex,
    ) -> int:
        """Listings still without a model go to the group of the description in their title.

        A description is no model, so this runs after mining: "Neck Logo" is found first and
        only the rest ("Baggy", "Wide Leg") is described. Groups are created on demand,
        one per phrase and product type; ``ignored`` ones are tombstones and stay empty.
        """

        existing = {(group.product_type, group.slug): group for group in groups.values()}
        wanted: dict[tuple[str, str], tuple[Descriptor, list[int]]] = {}
        for row in rows:
            decision = results[row.id]
            if decision.method != "none":
                continue
            assert row.normalized is not None
            match = descriptors.find(decision.product_type, row.normalized.tokens)
            if match is None:
                continue
            key = (decision.product_type, match.descriptor.slug)
            if key in existing and existing[key].status == "ignored":
                continue
            wanted.setdefault(key, (match.descriptor, []))[1].append(row.id)
        lines = {
            key: (descriptor.name, descriptor.aliases, row_ids)
            for key, (descriptor, row_ids) in wanted.items()
        }
        return await self._system_lines(brand, groups, results, lines, "descriptor")

    async def _collaborate(
        self,
        brand: Brand,
        rows: Sequence[_Row],
        results: dict[int, _Decision],
        groups: dict[int, ModelGroup],
        collabs: CollabIndex,
    ) -> int:
        """Listings still without a model go to the line of the collaboration in their title.

        Only the title counts: the partner name or an alias from the seed file, looked up
        before the brand terms (which include them) are removed. Grailed ``designers`` are no
        evidence. This runs before descriptions: "Yeezy Gap Wide Leg" is no "Wide Leg".
        """

        if not collabs.collabs:
            return 0
        existing = {(group.product_type, group.slug): group for group in groups.values()}
        wanted: dict[tuple[str, str], tuple[Collab, list[int]]] = {}
        for row in rows:
            decision = results[row.id]
            if decision.method != "none":
                continue
            assert row.normalized is not None
            collab = collabs.find(row.normalized.spelled, decision.product_type)
            if collab is None:
                continue
            key = (decision.product_type, collab.slug)
            if key in existing and existing[key].status == "ignored":
                continue
            wanted.setdefault(key, (collab, []))[1].append(row.id)
        lines = {
            key: (collab.name, list(collab.aliases), row_ids)
            for key, (collab, row_ids) in wanted.items()
        }
        return await self._system_lines(brand, groups, results, lines, "collab")

    async def _system_lines(
        self,
        brand: Brand,
        groups: dict[int, ModelGroup],
        results: dict[int, _Decision],
        wanted: Mapping[tuple[str, str], tuple[str, list[str], list[int]]],
        method: str,
    ) -> int:
        """Put rows into service groups ``(type, slug) -> (name, aliases, rows)``.

        Groups are created on demand; a retired one comes back with its id.
        """

        existing = {(group.product_type, group.slug): group for group in groups.values()}
        now = self._now()
        created = 0
        for (product_type, slug), (name, aliases, _) in wanted.items():
            group = existing.get((product_type, slug))
            if group is None:
                group = ModelGroup(
                    brand_id=brand.id,
                    product_type=product_type,
                    slug=slug,
                    name=name,
                    aliases=aliases,
                    status="auto",
                    source="system",
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(group)
                existing[(product_type, slug)] = group
                created += 1
            else:
                if group.retired_at is not None:
                    group.retired_at = None
                if group.name != name or group.aliases != aliases:
                    group.name, group.aliases, group.updated_at = name, aliases, now
        if wanted:
            await self._session.flush()
        for (product_type, slug), (_, _, row_ids) in wanted.items():
            group = existing[(product_type, slug)]
            groups[group.id] = group
            for row_id in row_ids:
                results[row_id] = _Decision(product_type, group.id, method)
        return created

    async def _mine(
        self,
        brand: Brand,
        rows: Sequence[_Row],
        results: dict[int, _Decision],
        groups: dict[int, ModelGroup],
        normalizer: TitleNormalizer,
        *,
        protected: set[int],
        full: bool,
        generic_phrases: Sequence[tuple[str, ...]],
        descriptors: DescriptorIndex,
        scope: set[int] | None,
    ) -> tuple[set[int], set[int]]:
        """Derive mined groups from "No model" listings; return derived and newly created ids.

        A derived phrase reuses its group (same type and slug, or an alias of it), so ids
        survive passes; a retired group comes back with its id.
        """

        samples: dict[str, list[MiningSample]] = defaultdict(list)
        scope_grams: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        bulk = self._wholesale_sellers(rows)
        for row in rows:
            decision = results[row.id]
            if decision.method not in _NO_MODEL:
                continue
            assert row.normalized is not None
            if scope is not None and row.id in scope:
                tokens = row.normalized.tokens
                scope_grams[decision.product_type].update(
                    tokens[start : start + size]
                    for size in range(1, 4)
                    for start in range(len(tokens) - size + 1)
                )
            if row.seller in bulk:
                continue
            samples[decision.product_type].append(
                MiningSample(
                    seller=row.seller or f"listing:{row.id}",
                    tokens=row.normalized.tokens,
                    surfaces=row.normalized.surfaces,
                )
            )
        now = self._now()
        keep_min = max(1, math.ceil(self._policy.mining.keep_ratio * MIN_SELLERS))
        derived: set[int] = set()
        fresh: set[int] = set()
        for product_type, items in sorted(samples.items()):
            in_type = [
                group
                for group in groups.values()
                if group.product_type == product_type
                and not is_descriptor_slug(group.slug)
                and not is_collab_slug(group.slug)
            ]
            mined_groups = [group for group in in_type if _derived(group)]
            known = [
                group
                for group in in_type
                if not _derived(group)
                or group.id in protected
                or (not full and group.retired_at is None)
            ]
            excluded = {
                tokens
                for group in known
                for value in (group.name, *group.aliases)
                if (tokens := normalizer.phrase(value))
            }
            excluded |= {tuple(group.slug.split("-")) for group in known}
            keep = {
                tuple(group.slug.split("-"))
                for group in mined_groups
                if group.retired_at is None and group.parent_id is None
            }
            phrases = mine_phrases(
                items,
                excluded=excluded,
                generic=self._policy.words.generic,
                # A phrase made only of descriptions is no model, in this product type.
                generic_phrases=(*generic_phrases, *descriptors.phrases(product_type)),
                keep=keep,
                keep_min_sellers=keep_min,
            )
            if scope is not None:
                phrases = [item for item in phrases if item.tokens in scope_grams[product_type]]
            by_slug = {group.slug: group for group in mined_groups}
            by_alias: dict[tuple[str, ...], ModelGroup] = {}
            for group in mined_groups:
                for value in group.aliases:
                    if tokens := normalizer.phrase(value):
                        by_alias.setdefault(tokens, group)
            taken = {group.slug for group in in_type}
            lines = [
                (tokens, group)
                for group in known
                if group.parent_id is None
                and group.status != "ignored"
                and group.slug != NONE_SLUG
                and group.retired_at is None
                for tokens in [normalizer.phrase(group.name)]
                if tokens
            ]
            touched: list[tuple[tuple[str, ...], ModelGroup]] = []
            created: list[ModelGroup] = []
            fragments: list[ModelGroup] = []
            for mined in phrases:
                slug = _slug(mined.tokens, mined.name)
                reused = by_slug.get(slug)
                if reused is None and slug not in taken:
                    reused = by_alias.get(mined.tokens)
                if reused is not None and reused.id not in derived:
                    if reused.slug != slug:
                        # The rules now spell the phrase differently: keep the id, the old
                        # phrase stays an alias.
                        reused.aliases = _merged(
                            [*reused.aliases, reused.name, reused.slug.replace("-", " ")],
                            mined.name,
                        )
                        reused.slug = slug
                        reused.name = mined.name
                        reused.updated_at = now
                        taken.add(slug)
                    if reused.retired_at is not None:
                        reused.retired_at = None
                    if reused.support != mined.sellers:
                        reused.support = mined.sellers
                    derived.add(reused.id)
                    touched.append((mined.tokens, reused))
                    if mined.fragment:
                        fragments.append(reused)
                    continue
                if slug in taken:
                    continue
                group = ModelGroup(
                    brand_id=brand.id,
                    product_type=product_type,
                    slug=slug,
                    name=mined.name,
                    aliases=[" ".join(mined.tokens)],
                    status="auto",
                    source="mined",
                    support=mined.sellers,
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(group)
                created.append(group)
                taken.add(slug)
                touched.append((mined.tokens, group))
                if mined.fragment:
                    fragments.append(group)
            if not touched:
                continue
            await self._session.flush()
            fresh |= {group.id for group in created}
            # A fragment ("Speed" of "Speed Hunters") is no line for anything to hang under.
            fragment_ids = {group.id for group in fragments}
            candidates = [
                (tokens, group) for tokens, group in [*lines, *touched]
                if group.id not in fragment_ids
            ]
            modifiers = self._policy.words.protected_modifiers
            for tokens, group in touched:
                groups[group.id] = group
                derived.add(group.id)
                parent = parent_line(
                    tokens,
                    [(line, other) for line, other in candidates if other is not group],
                    modifiers,
                )
                parent_id = parent.id if parent is not None else None
                if group.parent_id != parent_id:
                    group.parent_id = parent_id
            for _, group in touched:
                parent = groups.get(group.parent_id) if group.parent_id else None
                if parent is not None and parent.parent_id is not None:
                    group.parent_id = parent.parent_id
        if derived:
            await self._session.flush()
        return derived, fresh

    def _wholesale_sellers(self, rows: Sequence[_Row]) -> set[str]:
        """Sellers whose volume (policy ``wholesale_listings``) keeps them out of mining."""

        limit = self._policy.mining.wholesale_listings
        if limit is None:
            return set()
        counts = Counter(row.seller for row in rows if row.seller)
        return {seller for seller, count in counts.items() if count >= limit}

    async def _inherit(
        self,
        rows: Sequence[_Row],
        results: dict[int, _Decision],
        groups: dict[int, ModelGroup],
        fresh: set[int],
        protected: set[int],
        normalizer: TitleNormalizer,
        forbidden: Callable[[str, Sequence[str]], bool],
    ) -> int:
        """A new group that took most listings of a vanished one keeps the old id.

        Rule changes respell phrases ("pander" becomes "xpander"); the group, its links and
        its metric history should survive that, with the old phrase kept as an alias.
        A phrase the rules now forbid ("Wide Leg" became a description) is no spelling of
        anything: it is not inherited and never kept as an alias, or it would be a model again.
        """

        if not fresh:
            return 0
        now = self._now()
        used = Counter(decision.group_id for decision in results.values())
        sources: dict[int, Counter[int]] = defaultdict(Counter)
        for row in rows:
            new_id = results[row.id].group_id
            if new_id in fresh and row.group_id is not None and row.group_id != new_id:
                sources[new_id][row.group_id] += 1
        claimed: set[int] = set()
        inherited = 0
        for new_id, previous in sorted(sources.items(), key=lambda item: (-used[item[0]], item[0])):
            new = groups[new_id]
            for old_id, count in previous.most_common():
                old = groups.get(old_id)
                if (
                    old is None
                    or old_id in claimed
                    or old_id in fresh
                    or old_id in protected
                    or not _derived(old)
                    or used[old_id]
                    or old.product_type != new.product_type
                    or forbidden(old.product_type, normalizer.phrase(old.name))
                ):
                    continue
                if count * 2 < used[new_id]:
                    break
                claimed.add(old_id)
                for decision in results.values():
                    if decision.group_id == new_id:
                        decision.group_id = old_id
                for group in groups.values():
                    if group.parent_id == new_id:
                        group.parent_id = old_id
                slug, name, parent_id, support = new.slug, new.name, new.parent_id, new.support
                aliases = [
                    item
                    for item in _merged(
                        [*old.aliases, old.name, old.slug.replace("-", " "), *new.aliases], name
                    )
                    if not forbidden(old.product_type, normalizer.phrase(item))
                ]
                await self._session.delete(new)
                del groups[new_id]
                fresh.discard(new_id)
                await self._session.flush()
                old.slug, old.name, old.aliases = slug, name, aliases
                old.parent_id = parent_id if parent_id != old_id else None
                old.support = support
                old.retired_at = None
                old.updated_at = now
                inherited += 1
                break
        if inherited:
            await self._session.flush()
        return inherited

    async def _settle(
        self,
        groups: dict[int, ModelGroup],
        results: dict[int, _Decision],
        protected: set[int],
        now: datetime,
        stats: BrandGroupingStats,
        was_retired: Mapping[int, datetime | None],
        *,
        full: bool,
        fresh: set[int],
    ) -> None:
        """Mined groups without listings retire; expired ones are deleted (full pass only).

        A delta pass is additive: it never retires a group that was active before it, it
        only puts back to rest groups it revived or created that got no listings.
        """

        used = {decision.group_id for decision in results.values()}
        used |= {
            parent
            for group_id in used
            if (group := groups.get(group_id)) is not None
            and (parent := group.parent_id) is not None
        }
        ttl = timedelta(days=self._policy.mining.retire_ttl_days)
        expired: list[ModelGroup] = []
        for group in groups.values():
            if not (_derived(group) or _system_made(group)) or group.id in protected:
                continue
            before = was_retired.get(group.id)
            if group.id in used:
                if group.retired_at is not None:
                    group.retired_at = None
                if before is not None:
                    stats.revived += 1
                continue
            if before is not None:
                if group.retired_at != before:
                    group.retired_at = before
            elif full or group.id in fresh:
                group.retired_at = now
                stats.retired += 1
            if full and group.retired_at is not None and now - _aware(group.retired_at) >= ttl:
                expired.append(group)
        if not expired:
            return
        doomed = {group.id for group in expired}
        for group in groups.values():
            if group.parent_id in doomed and group.id not in doomed:
                group.parent_id = None
        await self._session.execute(delete(GroupMetric).where(GroupMetric.group_id.in_(doomed)))
        for group in expired:
            await self._session.delete(group)
            del groups[group.id]
        stats.deleted += len(expired)
        await self._session.flush()

    async def _write(
        self,
        rows: Sequence[_Row],
        results: dict[int, _Decision],
        relists: dict[int, int],
    ) -> int:
        now = self._now()
        assignment_rows: list[dict[str, Any]] = []
        listing_rows: list[dict[str, Any]] = []
        for row in rows:
            decision = results[row.id]
            relist_of = relists.get(row.id)
            if (
                row.group_id != decision.group_id
                or row.method != decision.method
                or row.input_hash != row.new_hash
            ):
                assignment_rows.append(
                    {
                        "listing_id": row.id,
                        "model_group_id": decision.group_id,
                        "method": decision.method,
                        "input_hash": row.new_hash,
                        "updated_at": now,
                    }
                )
            if row.product_type != decision.product_type or row.relist_of_id != relist_of:
                listing_rows.append(
                    {"row_id": row.id, "type": decision.product_type, "relist": relist_of}
                )
        for start in range(0, len(assignment_rows), _WRITE_CHUNK):
            chunk = assignment_rows[start : start + _WRITE_CHUNK]
            statement = insert(ListingModelAssignment).values(chunk)
            await self._session.execute(
                statement.on_conflict_do_update(
                    index_elements=[ListingModelAssignment.listing_id],
                    set_={
                        "model_group_id": statement.excluded.model_group_id,
                        "method": statement.excluded.method,
                        "input_hash": statement.excluded.input_hash,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )
        if listing_rows:
            table = cast(Table, Listing.__table__)
            await self._session.execute(
                update(table)
                .where(table.c.id == bindparam("row_id"))
                .values(product_type=bindparam("type"), relist_of_id=bindparam("relist")),
                listing_rows,
            )
        return len({item["listing_id"] for item in assignment_rows} | {
            item["row_id"] for item in listing_rows
        })

    def _rules_hash(
        self,
        groups: dict[int, ModelGroup],
        rows: Sequence[_Row],
        blocked: Sequence[str],
        terms: Sequence[str],
        parents: Mapping[int, int | None],
    ) -> str:
        payload = {
            "version": GROUPING_VERSION,
            "revision": _RULES_REVISION,
            "policy": self._policy.digest,
            "terms": list(terms),
            "blocked": list(blocked),
            "groups": sorted(
                [
                    group.id,
                    group.product_type,
                    group.slug,
                    group.name,
                    list(group.aliases),
                    group.parent_id,
                    group.status,
                    group.infer,
                    group.retired_at is not None,
                ]
                for group in groups.values()
            ),
            "overrides": sorted([row.id, row.override] for row in rows if row.override),
            "parents": sorted([key, value] for key, value in parents.items()),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()


def _derived(group: ModelGroup) -> bool:
    """Mined groups are derived data: a full pass rebuilds them from listings and rules."""

    return group.status == "auto" and group.source == "mined"


def _system_made(group: ModelGroup) -> bool:
    """Description and collaboration groups: they retire when no title needs them."""

    return group.status == "auto" and (
        is_descriptor_slug(group.slug) or is_collab_slug(group.slug)
    )


def _protected(
    groups: Mapping[int, ModelGroup], rows: Sequence[_Row], parents: Mapping[int, int | None]
) -> set[int]:
    """Mined groups that manual rules point at are never retired (they stay "auto")."""

    ids = {row.override for row in rows if row.override is not None}
    for group_id, parent_id in parents.items():
        ids.add(group_id)
        if parent_id is not None:
            ids.add(parent_id)
    for group in groups.values():
        if group.parent_id is not None and not _derived(group) and group.status != "ignored":
            ids.add(group.parent_id)
    return {group_id for group_id in ids if group_id in groups and _derived(groups[group_id])}


def _apply_parents(groups: Mapping[int, ModelGroup], parents: Mapping[int, int | None]) -> None:
    """Manual line changes win over derived parents; invalid ones are skipped."""

    for group_id, parent_id in parents.items():
        group = groups.get(group_id)
        if group is None or group.parent_id == parent_id:
            continue
        if parent_id is not None:
            parent = groups.get(parent_id)
            if (
                parent is None
                or parent.id == group.id
                or parent.product_type != group.product_type
                or parent.parent_id is not None
                or parent.status == "ignored"
            ):
                continue
        group.parent_id = parent_id


def _merged(values: Iterable[str], name: str) -> list[str]:
    return [item for item in dict.fromkeys(values) if item and item != name]


def _input_hash(row: _Row) -> str:
    payload = "\x1f".join([row.title, row.category_path or "", *sorted(row.designers)])
    return hashlib.sha256(payload.encode()).hexdigest()


def _slug(tokens: Sequence[str], fallback: str) -> str:
    return "-".join(tokens) if tokens else slugify(fallback)


def _version_name(
    tokens: Sequence[str], parent: ModelGroup, normalizer: TitleNormalizer
) -> str:
    parent_tokens = normalizer.phrase(parent.name)
    size = len(parent_tokens)
    for start in range(len(tokens) - size + 1):
        if tuple(tokens[start : start + size]) == parent_tokens:
            before = " ".join(token.title() for token in tokens[:start])
            after = " ".join(token.upper() if token.startswith("#") else token
                             for token in tokens[start + size :])
            return " ".join(part for part in (before, parent.name, after) if part)
    return " ".join(token.title() for token in tokens)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
