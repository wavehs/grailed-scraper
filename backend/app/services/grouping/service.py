"""Regroup a brand's listings: product type, model line/version, mined models and relists."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Table, bindparam, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Brand,
    BrandSourceMap,
    BrandStopword,
    Listing,
    ListingModelAssignment,
    ListingOverride,
    ModelGroup,
)
from app.domain.listings import slugify
from app.services.grouping.assign import (
    ModelDictionary,
    ModelEntry,
    TypeDecision,
    classify_type,
)
from app.services.grouping.kinds import NONE_SLUG
from app.services.grouping.mining import MiningSample, contains, mine_phrases
from app.services.grouping.normalize import NormalizedTitle, TitleNormalizer
from app.services.grouping.policy import REVIEW_TYPE, GroupingPolicy, SeedModel, load_policy
from app.services.grouping.relists import RelistRow, detect_relists

GROUPING_VERSION = "grouping-v6"
NONE_NAME = "No model"
_WRITE_CHUNK = 500


@dataclass(slots=True)
class BrandGroupingStats:
    brand_id: int
    listings: int = 0
    processed: int = 0
    changed: int = 0
    with_model: int = 0
    no_model: int = 0
    review: int = 0
    groups_created: int = 0
    mined: int = 0
    relists: int = 0
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
            "review": self.review,
            "groups_created": self.groups_created,
            "mined": self.mined,
            "relists": self.relists,
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
            for key in ("listings", "changed", "with_model", "no_model", "review", "mined"):
                totals[key] += int(getattr(item, key))
            totals["groups_created"] += item.groups_created
            totals["relists"] += item.relists
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

    def __init__(self, session: AsyncSession, policy: GroupingPolicy | None = None) -> None:
        self._session = session
        self._policy = policy or load_policy()

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
        rows = await self._rows(brand_id)
        stats.listings = len(rows)
        designers = sorted({name for row in rows for name in row.designers})
        mappings = await self._session.scalars(
            select(BrandSourceMap.source_designer_name).where(
                BrandSourceMap.brand_id == brand_id, BrandSourceMap.rejected_at.is_(None)
            )
        )
        stopwords = sorted(
            await self._session.scalars(
                select(BrandStopword.phrase).where(BrandStopword.brand_id == brand_id)
            )
        )
        terms = list(dict.fromkeys([brand.name, *brand.aliases, *mappings, *designers]))
        normalizer = TitleNormalizer(
            self._policy, brand_terms=terms, brand_slug=brand.slug, stopwords=stopwords
        )
        groups = {
            group.id: group
            for group in await self._session.scalars(
                select(ModelGroup).where(ModelGroup.brand_id == brand_id)
            )
        }
        seeds = self._policy.seed_models(brand.slug)
        stats.groups_created += await self._seed(brand, seeds, groups, normalizer, None)

        cache: dict[str, NormalizedTitle] = {}
        for row in rows:
            if row.title not in cache:
                cache[row.title] = normalizer.normalize(row.title)
            row.normalized = cache[row.title]
            row.new_hash = _input_hash(row)

        rules_hash = self._rules_hash(groups, rows, stopwords, terms)
        incremental = not full and brand.grouping_hash == rules_hash
        stats.full = not incremental
        weights = Counter(row.group_id for row in rows if row.group_id is not None)
        dictionary = self._dictionary(groups, normalizer, weights)
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
        created = await self._seed(brand, seeds, groups, normalizer, present)
        stats.groups_created += created
        if created:
            dictionary = self._dictionary(groups, normalizer, weights)
            incremental = False
            stats.full = True

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

        mined = await self._mine(brand, rows, results, groups, normalizer)
        stats.mined = mined
        if mined:
            stats.groups_created += mined
            dictionary = self._dictionary(groups, normalizer, weights)
            retry = [row for row in rows if results[row.id].method == "none"]
            again, extra = await self._assign(
                brand, retry, decisions, dictionary, groups, normalizer
            )
            stats.groups_created += extra
            results.update(again)

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
            else:
                stats.with_model += 1
        brand.grouping_hash = self._rules_hash(groups, rows, stopwords, terms)
        brand.grouped_at = datetime.now(UTC)
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
    ) -> int:
        """Create missing seed models; ``present`` limits "any" seeds to types in use."""

        existing = {(group.product_type, group.slug) for group in groups.values()}
        created: list[tuple[ModelGroup, str | None]] = []
        now = datetime.now(UTC)
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
                if (product_type, slug) in existing:
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
                existing.add((product_type, slug))
                created.append((group, seed.parent))
        if not created:
            return 0
        await self._session.flush()
        for group, _ in created:
            groups[group.id] = group
        by_name = {
            (group.product_type, group.name.casefold()): group for group in groups.values()
        }
        for group, parent_name in created:
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
    ) -> ModelDictionary:
        entries = []
        for group in groups.values():
            if group.status == "ignored" or group.slug == NONE_SLUG:
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
        now = datetime.now(UTC)
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

    async def _mine(
        self,
        brand: Brand,
        rows: Sequence[_Row],
        results: dict[int, _Decision],
        groups: dict[int, ModelGroup],
        normalizer: TitleNormalizer,
    ) -> int:
        samples: dict[str, list[MiningSample]] = defaultdict(list)
        for row in rows:
            decision = results[row.id]
            if decision.method != "none":
                continue
            assert row.normalized is not None
            samples[decision.product_type].append(
                MiningSample(
                    seller=row.seller or f"listing:{row.id}",
                    tokens=row.normalized.tokens,
                    surfaces=row.normalized.surfaces,
                )
            )
        created = 0
        now = datetime.now(UTC)
        for product_type, items in sorted(samples.items()):
            known = [group for group in groups.values() if group.product_type == product_type]
            excluded = {
                tokens
                for group in known
                for value in (group.name, *group.aliases)
                if (tokens := normalizer.phrase(value))
            }
            excluded |= {tuple(group.slug.split("-")) for group in known}
            phrases = mine_phrases(
                items, excluded=excluded, generic=self._policy.words.generic
            )
            lines = [
                (tokens, group)
                for group in known
                if group.parent_id is None
                and group.status != "ignored"
                and group.slug != NONE_SLUG
                for tokens in [normalizer.phrase(group.name)]
                if tokens
            ]
            new_groups: list[tuple[tuple[str, ...], ModelGroup]] = []
            for mined in phrases:
                slug = _slug(mined.tokens, mined.name)
                if any(group.slug == slug for group in known):
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
                new_groups.append((mined.tokens, group))
                known.append(group)
            if not new_groups:
                continue
            await self._session.flush()
            candidates = [*lines, *new_groups]
            for tokens, group in new_groups:
                groups[group.id] = group
                parents = [
                    (len(line_tokens), parent)
                    for line_tokens, parent in candidates
                    if parent is not group and contains(tokens, line_tokens)
                ]
                if parents:
                    group.parent_id = max(parents, key=lambda item: item[0])[1].id
            for _, group in new_groups:
                parent = groups.get(group.parent_id) if group.parent_id else None
                if parent is not None and parent.parent_id is not None:
                    group.parent_id = parent.parent_id
            created += len(new_groups)
        if created:
            await self._session.flush()
        return created

    async def _write(
        self,
        rows: Sequence[_Row],
        results: dict[int, _Decision],
        relists: dict[int, int],
    ) -> int:
        now = datetime.now(UTC)
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
        stopwords: Sequence[str],
        terms: Sequence[str],
    ) -> str:
        payload = {
            "version": GROUPING_VERSION,
            "policy": self._policy.digest,
            "terms": list(terms),
            "stopwords": list(stopwords),
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
                ]
                for group in groups.values()
            ),
            "overrides": sorted([row.id, row.override] for row in rows if row.override),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()


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
