"""Explainable model resolution and same-seller pre-sale relist detection."""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from rapidfuzz.fuzz import token_set_ratio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.db.models import (
    IdentityMatch,
    Listing,
    ListingModelAssignment,
    ModelGroup,
    ModelRule,
    ParserRunTask,
    PhysicalItem,
    PhysicalItemMember,
)
from app.services.identity.images import fingerprint_url, hamming_distance
from app.services.scoring.service import rule_matches
from app.services.transport.protocols import HttpTransport
from app.services.transport.rate_limiter import RateLimiter

IDENTITY_VERSION = "identity-v4"
_GENERIC = {
    "authentic",
    "brand",
    "condition",
    "description",
    "drop",
    "final",
    "grail",
    "grailed",
    "new",
    "price",
    "rare",
    "read",
    "repost",
    "sale",
    "used",
    "vintage",
    "qs",
}
_VARIANT_TOKENS = {
    "beige",
    "bk",
    "black",
    "blue",
    "brown",
    "colour",
    "color",
    "cream",
    "green",
    "grey",
    "gray",
    "large",
    "medium",
    "multi",
    "orange",
    "pink",
    "purple",
    "red",
    "silver",
    "small",
    "slt",
    "white",
    "yellow",
    "gold",
    "xxs",
    "xs",
    "xxl",
    "xl",
}
# Product descriptors alone are not enough evidence to merge two listings.
_PRODUCT_TERMS = frozenset(
    """accessories accessory anklet backpack bag band baseball beanie belt blazer boot boots
    bottom bottoms bracelet cap cardigan chain charm coat cotton crewneck cross denim dress
    earring earrings footwear glasses glove gloves gold graphic hat hoodie jacket jean jeans
    jogger joggers keychain leather loafer loafers logo long men mens necklace pant pants
    pendant pocket print ring scarf shirt shoe shoes short shorts silver sleeve sneaker sneakers
    sock socks sweater sweatpant sweatpants sweatshirt tank tee top tops unisex vest wallet watch
    women womens wool zip zipper zipup""".split()
)
_STOP_WORDS = frozenset(
    "a an and at by for from in is of on or the to w with men mens women womens unisex".split()
)
_MODEL_NOISE = frozenset(
    """14k 18k 22k 925 950 999 ball chain chains cuban gold leather link links metal
    rhodium rope rubber silver sterling suede wool cotton denim""".split()
)
_NON_DISTINCTIVE_MODEL = frozenset(
    "classic embroidered fitted graphic logo long plain pocket print short sleeve".split()
)
_TOKEN_NORMALIZATION = {
    "bags": "bag",
    "boots": "boot",
    "bracelets": "bracelet",
    "caps": "cap",
    "charms": "charm",
    "coats": "coat",
    "earrings": "earring",
    "glasses": "eyewear",
    "hats": "hat",
    "hoodies": "hoodie",
    "jackets": "jacket",
    "jeans": "jean",
    "keychains": "keychain",
    "loafers": "loafer",
    "necklaces": "necklace",
    "pants": "pant",
    "pendent": "pendant",
    "pendents": "pendant",
    "pendants": "pendant",
    "rings": "ring",
    "shirts": "shirt",
    "shoes": "shoe",
    "shorts": "short",
    "slippers": "slipper",
    "sneakers": "sneaker",
    "sunglasses": "eyewear",
    "sweaters": "sweater",
    "tees": "tee",
    "wallets": "wallet",
    "watches": "watch",
}
_FAMILIES: tuple[tuple[str, frozenset[str]], ...] = (
    ("keychain", frozenset({"keychain"})),
    ("bracelet", frozenset({"bracelet", "bangle"})),
    ("ring", frozenset({"ring"})),
    ("earring", frozenset({"earring"})),
    ("eyewear", frozenset({"eyewear", "frame"})),
    ("watch", frozenset({"watch"})),
    ("belt", frozenset({"belt"})),
    ("wallet", frozenset({"wallet", "cardholder"})),
    ("bag", frozenset({"bag", "backpack", "duffle", "tote"})),
    ("hat", frozenset({"hat", "cap", "beanie", "snapback"})),
    ("footwear", frozenset({"boot", "loafer", "sandal", "shoe", "slipper", "sneaker"})),
    ("hoodie", frozenset({"hoodie", "zipup"})),
    ("sweatshirt", frozenset({"crewneck", "sweatshirt"})),
    ("jacket", frozenset({"blazer", "coat", "jacket"})),
    ("sweater", frozenset({"cardigan", "sweater"})),
    ("jeans", frozenset({"jean"})),
    ("pants", frozenset({"jogger", "pant", "sweatpant"})),
    ("shorts", frozenset({"short"})),
    ("dress", frozenset({"dress", "skirt"})),
    ("tee", frozenset({"shirt", "tank", "tee"})),
    ("necklace", frozenset({"charm", "necklace", "pendant"})),
)
_FAMILY_TOKENS = frozenset(token for _, tokens in _FAMILIES for token in tokens)
_CATEGORY_FAMILY = {
    "bottoms": "bottom",
    "footwear": "footwear",
    "outerwear": "jacket",
    "tops": "top",
    "womens_bottoms": "bottom",
    "womens_dresses": "dress",
    "womens_footwear": "footwear",
    "womens_jewelry": "necklace",
    "womens_outerwear": "jacket",
    "womens_tops": "top",
}
_TOKEN = re.compile(r"[a-z0-9]+", re.I)
_SIZE = re.compile(
    r"\b(?:size|sz|eu|us|uk)\s*[:\-]?\s*(?:xx?s|s|m|l|xxl|\d{1,3}(?:\.\d+)?)\b",
    re.I,
)


class IdentityResolver:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        transport: HttpTransport | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._transport = transport
        self._match_cache: dict[tuple[str, int, int], IdentityMatch] | None = None

    async def resolve_run(self, run_id: int) -> dict[str, int | str]:
        brand_ids = set(
            await self._session.scalars(
                select(ParserRunTask.brand_id).where(
                    ParserRunTask.run_id == run_id, ParserRunTask.brand_id.is_not(None)
                )
            )
        )
        if not brand_ids:
            return {"version": IDENTITY_VERSION, "listings": 0, "pending": 0, "linked": 0}
        listings = list(
            await self._session.scalars(
                select(Listing)
                .where(Listing.brand_id.in_(brand_ids))
                .order_by(Listing.brand_id, Listing.created_at, Listing.id)
            )
        )
        retired_candidates = await self._retire_stale_candidates()
        await self._backfill(listings)
        await self._assign_models(listings)
        model = await self._model_candidates(listings)
        physical = await self._physical_candidates(listings, run_id)
        candidates = [*model, *physical]
        image_requests = await self._fingerprint_candidates(candidates)
        await self._reevaluate_candidates(candidates)
        await self.rebuild_physical_items()
        await self._session.flush()
        pending = int(
            await self._session.scalar(
                select(func.count(IdentityMatch.id)).where(IdentityMatch.status == "pending")
            )
            or 0
        )
        linked = int(
            await self._session.scalar(
                select(func.count(IdentityMatch.id)).where(
                    IdentityMatch.status.in_(("auto_confirmed", "confirmed"))
                )
            )
            or 0
        )
        return {
            "version": IDENTITY_VERSION,
            "listings": len(listings),
            "pending": pending,
            "linked": linked,
            "image_requests": image_requests,
            "retired_candidates": retired_candidates,
        }

    async def decide(
        self,
        match_id: int,
        decision: Literal["confirmed", "rejected"],
    ) -> IdentityMatch:
        match = await self._session.get(IdentityMatch, match_id)
        if match is None:
            raise LookupError("identity_match_not_found")
        now = datetime.now(UTC)
        match.status = decision
        match.reviewed_at = now
        match.updated_at = now
        if decision == "confirmed" and match.level == "model":
            await self._confirm_model(match.left_listing_id, match.right_listing_id)
        if match.level == "physical":
            await self.rebuild_physical_items()
        await self._session.flush()
        return match

    async def rebuild_physical_items(self) -> None:
        edges = list(
            (
                await self._session.execute(
                    select(IdentityMatch.left_listing_id, IdentityMatch.right_listing_id).where(
                        IdentityMatch.level == "physical",
                        IdentityMatch.status.in_(("auto_confirmed", "confirmed")),
                    )
                )
            ).tuples()
        )
        parent: dict[int, int] = {}

        def root(value: int) -> int:
            parent.setdefault(value, value)
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        for left, right in edges:
            left_root, right_root = root(left), root(right)
            if left_root != right_root:
                parent[right_root] = left_root
        components: dict[int, set[int]] = defaultdict(set)
        for listing_id in parent:
            components[root(listing_id)].add(listing_id)
        desired = sorted(components.values(), key=lambda members: min(members))
        current_items = {
            item.id: item for item in await self._session.scalars(select(PhysicalItem))
        }
        current_members = list(await self._session.scalars(select(PhysicalItemMember)))
        current_by_item: dict[int, set[int]] = defaultdict(set)
        current_item_by_listing: dict[int, int] = {}
        for membership in current_members:
            current_by_item[membership.physical_item_id].add(membership.listing_id)
            current_item_by_listing[membership.listing_id] = membership.physical_item_id
        if {frozenset(members) for members in current_by_item.values()} == {
            frozenset(members) for members in desired
        } and len(current_items) == len(desired):
            return

        now = datetime.now(UTC)
        used_item_ids: set[int] = set()
        desired_item_by_listing: dict[int, int] = {}
        for members in desired:
            candidates = Counter(
                current_item_by_listing[listing_id]
                for listing_id in members
                if listing_id in current_item_by_listing
                and current_item_by_listing[listing_id] not in used_item_ids
            )
            highest_overlap = max(candidates.values(), default=0)
            item_id = min(
                (candidate for candidate, count in candidates.items() if count == highest_overlap),
                default=None,
            )
            if item_id is None:
                item = PhysicalItem(created_at=now, updated_at=now)
                self._session.add(item)
                await self._session.flush()
                item_id = item.id
                current_items[item_id] = item
            elif current_by_item[item_id] != members:
                current_items[item_id].updated_at = now
            used_item_ids.add(item_id)
            desired_item_by_listing.update(dict.fromkeys(members, item_id))

        for membership in current_members:
            if desired_item_by_listing.get(membership.listing_id) != membership.physical_item_id:
                await self._session.delete(membership)
        await self._session.flush()
        self._session.add_all(
            PhysicalItemMember(listing_id=listing_id, physical_item_id=item_id, added_at=now)
            for listing_id, item_id in desired_item_by_listing.items()
            if current_item_by_listing.get(listing_id) != item_id
        )
        for item_id, item in current_items.items():
            if item_id not in used_item_ids:
                await self._session.delete(item)

    async def _retire_stale_candidates(self) -> int:
        result = await self._session.execute(
            update(IdentityMatch)
            .where(
                IdentityMatch.status == "pending",
                IdentityMatch.algorithm_version != IDENTITY_VERSION,
            )
            .values(status="rejected", updated_at=datetime.now(UTC))
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def _backfill(self, listings: Sequence[Listing]) -> None:
        for index, listing in enumerate(listings):
            if index % 250 == 0:
                await asyncio.sleep(0)
            raw = listing.raw_json if isinstance(listing.raw_json, dict) else {}
            listing.source_product_id = listing.source_product_id or _positive_int(
                raw.get("product_id")
            )
            listing.source_sku_id = listing.source_sku_id or _positive_int(raw.get("sku_id"))
            listing.source_repost_id = listing.source_repost_id or _positive_int(
                raw.get("repost_id")
            )
            listing.color = listing.color or _clean(raw.get("color"))
            if listing.cover_asset_key is None:
                cover_value = raw.get("cover_photo")
                cover: dict[str, Any] = cover_value if isinstance(cover_value, dict) else {}
                listing.cover_asset_key = asset_key(
                    _clean(cover.get("image_url"))
                    or _clean(cover.get("url"))
                    or listing.cover_photo_url
                )
            listing.identity_version = IDENTITY_VERSION

    async def _assign_models(self, listings: Sequence[Listing]) -> None:
        rules = list(
            await self._session.scalars(
                select(ModelRule)
                .where(ModelRule.is_active.is_(True))
                .options(selectinload(ModelRule.group))
                .order_by(ModelRule.id)
            )
        )
        by_brand: dict[int, list[ModelRule]] = defaultdict(list)
        for rule in rules:
            by_brand[rule.brand_id].append(rule)
        group_rows = list(await self._session.scalars(select(ModelGroup)))
        groups = {
            group.stable_key: group
            for group in group_rows
            if group.group_type in {"source_product", "resolved"}
        }
        groups_by_id = {group.id: group for group in group_rows}
        existing = {
            row.listing_id: row
            for row in await self._session.scalars(select(ListingModelAssignment))
        }
        now = datetime.now(UTC)
        decisions: list[tuple[Listing, ModelGroup, str]] = []
        for index, listing in enumerate(listings):
            if index % 250 == 0:
                await asyncio.sleep(0)
            assignment = existing.get(listing.id)
            if assignment is not None and assignment.method == "manual":
                continue
            if listing.brand_id is None:
                continue
            matches = [
                rule
                for rule in by_brand.get(listing.brand_id or -1, [])
                if rule_matches(rule, listing.title, listing.category)
            ]
            group: ModelGroup | None = None
            method = ""
            if matches:
                winner = min(matches, key=lambda item: (-len(item.include_keywords), item.id))
                group, method = winner.group, "rule"
            else:
                signature = model_signature(
                    listing.title,
                    listing.brand_name_raw,
                    listing.size_raw,
                    listing.color,
                    listing.category,
                )
                if signature is not None:
                    family, core = signature
                    stable_key = f"resolved-v4:{listing.brand_id}:{family}:{core}"
                    group = groups.get(stable_key)
                    if group is None:
                        group = ModelGroup(
                            stable_key=stable_key,
                            brand_id=listing.brand_id,
                            name=model_name(signature)[:255],
                            category=listing.category,
                            group_type="resolved",
                            created_at=now,
                            updated_at=now,
                        )
                        self._session.add(group)
                        groups[stable_key] = group
                    method = "model_signature"
            if group is None and listing.source_product_id:
                stable_key = (
                    f"source:{listing.source}:{listing.brand_id}:product:"
                    f"{listing.source_product_id}"
                )
                group = groups.get(stable_key)
                if group is None:
                    group = ModelGroup(
                        stable_key=stable_key,
                        brand_id=listing.brand_id,
                        name=listing.title[:255],
                        category=listing.category,
                        group_type="source_product",
                        created_at=now,
                        updated_at=now,
                    )
                    self._session.add(group)
                    groups[stable_key] = group
                method = "source_product_id"
            if group is None:
                stable_key = (
                    f"resolved:{listing.brand_id}:{listing.category or ''}:"
                    f"listing:{listing.grailed_id}"
                )
                group = groups.get(stable_key)
                if group is None:
                    group = ModelGroup(
                        stable_key=stable_key,
                        brand_id=listing.brand_id,
                        name=listing.title[:255],
                        category=listing.category,
                        group_type="resolved",
                        created_at=now,
                        updated_at=now,
                    )
                    self._session.add(group)
                    groups[stable_key] = group
                method = "unique_listing"
            decisions.append((listing, group, method))
        await self._session.flush()
        merge_targets: dict[int, set[int]] = defaultdict(set)
        for listing, group, method in decisions:
            assignment = existing.get(listing.id)
            if assignment is None:
                assignment = ListingModelAssignment(
                    listing_id=listing.id,
                    model_group_id=group.id,
                    method=method,
                    confidence=Decimal(1),
                    algorithm_version=IDENTITY_VERSION,
                    updated_at=now,
                )
                self._session.add(assignment)
                existing[listing.id] = assignment
            elif assignment.method != "manual" and (
                assignment.model_group_id != group.id
                or assignment.method != method
                or assignment.confidence != Decimal(1)
                or assignment.algorithm_version != IDENTITY_VERSION
            ):
                if assignment.model_group_id != group.id:
                    merge_targets[assignment.model_group_id].add(group.id)
                assignment.model_group_id = group.id
                assignment.method = method
                assignment.confidence = Decimal(1)
                assignment.algorithm_version = IDENTITY_VERSION
                assignment.updated_at = now
        for old_group_id, targets in merge_targets.items():
            if len(targets) == 1 and old_group_id not in targets:
                old_group = groups_by_id.get(old_group_id)
                if old_group is not None and old_group.group_type != "rule":
                    old_group.merged_into_id = next(iter(targets))
                    old_group.updated_at = now

    async def _model_candidates(self, listings: Sequence[Listing]) -> list[IdentityMatch]:
        assignments = {
            row.listing_id: row
            for row in await self._session.scalars(select(ListingModelAssignment))
        }
        buckets: dict[tuple[int | None, str], list[Listing]] = defaultdict(list)
        canonical: dict[int, str] = {}
        for listing in listings:
            assignment = assignments.get(listing.id)
            if assignment is not None and assignment.method in {
                "manual",
                "model_signature",
                "rule",
            }:
                continue
            text = model_text(
                listing.title, listing.brand_name_raw, listing.size_raw, listing.color
            )
            family = _product_family(text, listing.category)
            if family is None:
                continue
            canonical[listing.id] = text
            buckets[(listing.brand_id, family)].append(listing)
        matches: list[IdentityMatch] = []
        for group in buckets.values():
            texts = {item.id: canonical[item.id] for item in group}
            token_frequency = Counter(
                token for value in texts.values() for token in set(value.split())
            )
            inverted: dict[str, list[Listing]] = defaultdict(list)
            for item in group:
                for token in texts[item.id].split():
                    if token not in _GENERIC:
                        inverted[token].append(item)
            for listing_index, listing in enumerate(group):
                if listing_index % 250 == 0:
                    await asyncio.sleep(0)
                candidates: dict[int, Listing] = {}
                tokens = sorted(
                    set(texts[listing.id].split()) - _GENERIC,
                    key=lambda token: token_frequency[token],
                )[:3]
                for token in tokens:
                    for candidate in inverted[token]:
                        if candidate.id != listing.id:
                            candidates[candidate.id] = candidate
                ranked = sorted(
                    (
                        (
                            token_set_ratio(texts[listing.id], texts[candidate.id]),
                            candidate,
                        )
                        for candidate in candidates.values()
                        if (
                            assignments.get(listing.id) is None
                            or assignments.get(candidate.id) is None
                            or assignments[listing.id].model_group_id
                            != assignments[candidate.id].model_group_id
                        )
                    ),
                    key=lambda item: (item[0], item[1].id),
                )
                for score, candidate in ranked[-5:]:
                    if score >= 90 and _distinctive_overlap(texts[listing.id], texts[candidate.id]):
                        matches.append(await self._upsert_match(
                            "model",
                            listing,
                            candidate,
                            status="pending",
                            confidence=Decimal(str(score / 100)),
                            evidence={"title_similarity": score, "method": "model_text"},
                        ))
        return list(
            {(item.left_listing_id, item.right_listing_id): item for item in matches}.values()
        )

    async def _physical_candidates(
        self, listings: Sequence[Listing], run_id: int
    ) -> list[IdentityMatch]:
        by_seller: dict[str, list[Listing]] = defaultdict(list)
        for listing in listings:
            if listing.seller_identity:
                by_seller[listing.seller_identity].append(listing)
        candidates: list[IdentityMatch] = []
        for seller_listings in by_seller.values():
            ordered = sorted(seller_listings, key=_created)
            sale_times = sorted(
                _aware(item.sold_at or item.updated_at or item.last_seen_at)
                for item in ordered
                if item.status == "sold"
            )
            for index, current in enumerate(ordered):
                if index % 250 == 0:
                    await asyncio.sleep(0)
                if current.parser_run_id != run_id:
                    continue
                for previous in reversed(ordered[max(0, index - 50) : index]):
                    if previous.brand_id != current.brand_id:
                        continue
                    if any(
                        _aware(_created(previous)) < sold_at <= _aware(_created(current))
                        for sold_at in sale_times
                    ):
                        continue
                    if (
                        previous.category
                        and current.category
                        and previous.category != current.category
                    ):
                        continue
                    age = _created(current) - _created(previous)
                    exact_asset = bool(
                        previous.cover_asset_key
                        and previous.cover_asset_key == current.cover_asset_key
                    )
                    if age > timedelta(days=180) and not exact_asset:
                        continue
                    title = token_set_ratio(
                        model_text(
                            previous.title,
                            previous.brand_name_raw,
                            previous.size_raw,
                            previous.color,
                        ),
                        model_text(
                            current.title,
                            current.brand_name_raw,
                            current.size_raw,
                            current.color,
                        ),
                    )
                    price_delta = _price_delta(previous, current)
                    nonoverlap = previous.status in {"removed", "removed_pending"} and (
                        _aware(previous.last_seen_at) <= _aware(_created(current))
                    )
                    if not nonoverlap:
                        continue
                    status: Literal["pending", "auto_confirmed"] | None = None
                    confidence = Decimal(0)
                    if (
                        exact_asset
                        and nonoverlap
                        and title >= 85
                        and price_delta <= Decimal("0.30")
                    ):
                        status, confidence = "auto_confirmed", Decimal("0.9900")
                    elif (
                        age <= timedelta(days=30) and title >= 95 and price_delta < Decimal("0.10")
                    ):
                        status, confidence = "pending", Decimal("0.8000")
                    elif (
                        age <= timedelta(days=180)
                        and title >= 80
                        and price_delta <= Decimal("0.30")
                    ):
                        status, confidence = "pending", Decimal("0.7000")
                    if status is None:
                        continue
                    match = await self._upsert_match(
                        "physical",
                        previous,
                        current,
                        status=status,
                        confidence=confidence,
                        evidence={
                            "method": "same_seller_relist",
                            "same_seller": True,
                            "exact_asset": exact_asset,
                            "title_similarity": title,
                            "price_delta": str(price_delta),
                            "nonoverlap": nonoverlap,
                            "days_apart": age.days,
                        },
                        relation_type="relist",
                    )
                    candidates.append(match)
        return list(
            {(item.left_listing_id, item.right_listing_id): item for item in candidates}.values()
        )

    async def _fingerprint_candidates(self, matches: Sequence[IdentityMatch]) -> int:
        if self._transport is None or self._settings.identity_image_requests_per_run == 0:
            return 0
        ids = {
            value for match in matches for value in (match.left_listing_id, match.right_listing_id)
        }
        listings = {
            item.id: item
            for item in await self._session.scalars(select(Listing).where(Listing.id.in_(ids)))
        }
        limiter = RateLimiter(
            requests_per_minute=min(self._settings.requests_per_minute, 90),
            max_concurrent_per_host=1,
            burst=1,
            jitter_ratio=0,
        )
        requests = 0
        for listing in listings.values():
            if requests >= self._settings.identity_image_requests_per_run:
                break
            if listing.cover_dhash or not listing.cover_photo_url:
                continue
            fingerprint = await fingerprint_url(self._transport, limiter, listing.cover_photo_url)
            requests += 1
            if fingerprint is not None:
                listing.cover_content_sha256 = fingerprint.content_sha256
                listing.cover_dhash = fingerprint.dhash
        return requests

    async def _reevaluate_candidates(self, matches: Sequence[IdentityMatch]) -> None:
        ids = {
            value for match in matches for value in (match.left_listing_id, match.right_listing_id)
        }
        listings = {
            item.id: item
            for item in await self._session.scalars(select(Listing).where(Listing.id.in_(ids)))
        }
        for match in matches:
            if match.status in {"confirmed", "rejected"}:
                continue
            left, right = listings[match.left_listing_id], listings[match.right_listing_id]
            distance = (
                hamming_distance(left.cover_dhash, right.cover_dhash)
                if left.cover_dhash and right.cover_dhash
                else None
            )
            content_equal = bool(
                left.cover_content_sha256
                and left.cover_content_sha256 == right.cover_content_sha256
            )
            evidence = {
                **match.evidence,
                "image_distance": distance,
                "content_equal": content_equal,
            }
            match.evidence = evidence
            if match.level == "model":
                if (
                    (content_equal or distance is not None and distance <= 4)
                    and int(evidence.get("title_similarity", 0)) >= 92
                ):
                    match.status = "auto_confirmed"
                    match.confidence = Decimal("0.9800")
                    await self._confirm_model(match.left_listing_id, match.right_listing_id)
                elif distance is not None and distance <= 8:
                    match.confidence = max(match.confidence, Decimal("0.8500"))
                match.updated_at = datetime.now(UTC)
                continue
            if (
                (content_equal or distance is not None and distance <= 4)
                and bool(evidence.get("nonoverlap"))
                and int(evidence.get("title_similarity", 0)) >= 90
                and Decimal(str(evidence.get("price_delta", "1"))) <= Decimal("0.25")
            ):
                match.status = "auto_confirmed"
                match.confidence = Decimal("0.9800")
            elif distance is not None and distance <= 8:
                match.confidence = max(match.confidence, Decimal("0.8500"))
            match.updated_at = datetime.now(UTC)

    async def _upsert_match(
        self,
        level: Literal["model", "physical"],
        first: Listing,
        second: Listing,
        *,
        status: Literal["pending", "auto_confirmed"],
        confidence: Decimal,
        evidence: dict[str, Any],
        relation_type: Literal["relist"] | None = None,
    ) -> IdentityMatch:
        left, right = sorted((first.id, second.id))
        if self._match_cache is None:
            self._match_cache = {
                (item.level, item.left_listing_id, item.right_listing_id): item
                for item in await self._session.scalars(select(IdentityMatch))
            }
        key = (level, left, right)
        match = self._match_cache.get(key)
        now = datetime.now(UTC)
        if match is None:
            match = IdentityMatch(
                level=level,
                left_listing_id=left,
                right_listing_id=right,
                relation_type=relation_type,
                status=status,
                confidence=confidence,
                evidence=evidence,
                algorithm_version=IDENTITY_VERSION,
                created_at=now,
                updated_at=now,
            )
            self._session.add(match)
            self._match_cache[key] = match
        elif match.status not in {"confirmed", "rejected"} and (
            match.status != status
            or match.confidence != confidence
            or match.evidence != evidence
            or match.algorithm_version != IDENTITY_VERSION
        ):
            match.status = status
            match.confidence = confidence
            match.evidence = evidence
            match.algorithm_version = IDENTITY_VERSION
            match.updated_at = now
        return match

    async def _confirm_model(self, left_id: int, right_id: int) -> None:
        assignments = {
            row.listing_id: row
            for row in await self._session.scalars(
                select(ListingModelAssignment).where(
                    ListingModelAssignment.listing_id.in_((left_id, right_id))
                )
            )
        }
        now = datetime.now(UTC)
        group_ids = sorted({row.model_group_id for row in assignments.values()})
        if not group_ids:
            listing = await self._session.get(Listing, left_id)
            assert listing is not None and listing.brand_id is not None
            group = ModelGroup(
                stable_key=f"manual:{min(left_id, right_id)}",
                brand_id=listing.brand_id,
                name=listing.title[:255],
                category=listing.category,
                group_type="resolved",
                created_at=now,
                updated_at=now,
            )
            self._session.add(group)
            await self._session.flush()
            target_group_id = group.id
        else:
            target_group_id = group_ids[0]
            for source_group_id in group_ids[1:]:
                source = await self._session.get(ModelGroup, source_group_id)
                if source is not None:
                    source.merged_into_id = target_group_id
                    source.updated_at = now
                source_assignments = list(
                    await self._session.scalars(
                        select(ListingModelAssignment).where(
                            ListingModelAssignment.model_group_id == source_group_id
                        )
                    )
                )
                for source_assignment in source_assignments:
                    source_assignment.model_group_id = target_group_id
                    source_assignment.method = "manual"
                    source_assignment.confidence = Decimal(1)
                    source_assignment.algorithm_version = IDENTITY_VERSION
                    source_assignment.updated_at = now
        for listing_id in (left_id, right_id):
            assignment = assignments.get(listing_id)
            if assignment is None:
                self._session.add(
                    ListingModelAssignment(
                        listing_id=listing_id,
                        model_group_id=target_group_id,
                        method="manual",
                        confidence=Decimal(1),
                        algorithm_version=IDENTITY_VERSION,
                        updated_at=now,
                    )
                )
            else:
                assignment.model_group_id = target_group_id
                assignment.method = "manual"
                assignment.confidence = Decimal(1)
                assignment.algorithm_version = IDENTITY_VERSION
                assignment.updated_at = now


def model_text(
    title: str,
    brand: str | None = None,
    size: str | None = None,
    color: str | None = None,
) -> str:
    value = unicodedata.normalize("NFKD", _SIZE.sub(" ", title.casefold()))
    normalized = "".join(char for char in value if not unicodedata.combining(char))
    brand_tokens = set(_TOKEN.findall((brand or "").casefold()))
    variant_tokens = set(_TOKEN.findall(f"{size or ''} {color or ''}".casefold()))
    raw_tokens = _TOKEN.findall(normalized)
    tokens: list[str] = []
    index = 0
    while index < len(raw_tokens):
        token = _TOKEN_NORMALIZATION.get(raw_tokens[index], raw_tokens[index])
        if token == "t" and index + 1 < len(raw_tokens) and raw_tokens[index + 1] == "shirt":
            token = "tee"
            index += 1
        elif token in {"t", "tee", "tees", "tshirt", "tshirts"}:
            token = "tee"
        elif token in {"longsleeve", "longsleeves"}:
            for part in ("long", "sleeve"):
                if part not in brand_tokens and part not in variant_tokens:
                    tokens.append(part)
            index += 1
            continue
        elif token in {"shortsleeve", "shortsleeves"}:
            for part in ("short", "sleeve"):
                if part not in brand_tokens and part not in variant_tokens:
                    tokens.append(part)
            index += 1
            continue
        elif token == "sleeves":
            token = "sleeve"
        if (
            token not in brand_tokens
            and token not in _GENERIC
            and token not in _VARIANT_TOKENS
            and token not in variant_tokens
            and token not in {"size", "sz"}
        ):
            tokens.append(token)
        index += 1
    if "sleeve" in tokens:
        tokens = [token for token in tokens if token != "top"]
    return " ".join(tokens)


def model_signature(
    title: str,
    brand: str | None = None,
    size: str | None = None,
    color: str | None = None,
    category: str | None = None,
) -> tuple[str, str] | None:
    """Return an explainable, order-independent exact-model signature."""

    canonical = model_text(title, brand, size, color)
    family = _product_family(canonical, category)
    if family is None:
        return None
    core_tokens = sorted(
        set(canonical.split()) - _STOP_WORDS - _MODEL_NOISE - _FAMILY_TOKENS
    )
    if not any(
        token not in _NON_DISTINCTIVE_MODEL
        and (len(token) >= 3 or any(char.isdigit() for char in token))
        for token in core_tokens
    ):
        return None
    return family, " ".join(core_tokens)


def model_name(signature: tuple[str, str]) -> str:
    family, core = signature
    return core.title() if family in {"accessory", "bottom", "footwear", "top"} else (
        f"{core} {family}".title()
    )


def _product_family(canonical: str, category: str | None) -> str | None:
    tokens = set(canonical.split())
    for family, aliases in _FAMILIES:
        if tokens & aliases:
            return family
    return _CATEGORY_FAMILY.get((category or "").casefold())


def asset_key(value: str | None) -> str | None:
    if value is None:
        return None
    parts = urlsplit(value)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return None
    canonical = urlunsplit((parts.scheme.casefold(), parts.hostname.casefold(), parts.path, "", ""))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _distinctive_overlap(left: str, right: str) -> bool:
    return any(
        token not in _NON_DISTINCTIVE_MODEL
        and (len(token) >= 4 or any(char.isdigit() for char in token))
        for token in set(left.split()) & set(right.split())
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _created(listing: Listing) -> datetime:
    return _aware(listing.created_at or listing.first_seen_at)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _price_delta(left: Listing, right: Listing) -> Decimal:
    denominator = max(left.price, right.price)
    return abs(left.price - right.price) / denominator
