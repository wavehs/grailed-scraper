"""Explainable model resolution and same-seller pre-sale relist detection."""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from rapidfuzz.fuzz import token_set_ratio
from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.core.config import Settings
from app.db.models import (
    Brand,
    IdentityMatch,
    Listing,
    ListingModelAssignment,
    ModelGroup,
    ParserRunTask,
    PhysicalItem,
    PhysicalItemMember,
)
from app.services.ai_grouping.domain import (
    AI_KEY_PREFIX,
    GROUPING_VERSION,
    compute_input_hash,
    deterministic_product_type,
)
from app.services.identity.images import fingerprint_url, hamming_distance
from app.services.transport.protocols import HttpTransport
from app.services.transport.rate_limiter import RateLimiter

IDENTITY_VERSION = "identity-v5"
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
    "replica",
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
    "milk",
    "dust",
    "navy",
    "olive",
    "pearl",
    "chalk",
    "taupe",
    "burgundy",
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
    rhodium rope rubber silver sterling suede wool cotton denim high low mid top mega bumper
    jumbo lace laced mainline runway classic vintage short tongue fur""".split()
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
    "gats": "gat",
    "geobaskets": "geobasket",
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
    "accessories": "accessory",
    "bottoms": "bottom",
    "footwear": "footwear",
    "outerwear": "jacket",
    "tailoring": "tailoring",
    "tops": "top",
    "womens_accessories": "accessory",
    "womens_bags_luggage": "bag",
    "womens_bottoms": "bottom",
    "womens_dresses": "dress",
    "womens_footwear": "footwear",
    "womens_jewelry": "accessory",
    "womens_outerwear": "jacket",
    "womens_tops": "top",
}
_TOKEN = re.compile(r"[a-z0-9]+", re.I)
_SEASON_OR_YEAR = re.compile(r"^(?:19|20)\d{2}$|^(?:ss|fw|aw)\d{2,4}$", re.I)
_SIZE = re.compile(
    r"\b(?:size|sz|eu|us|uk)\s*[:\-]?\s*(?:xx?s|s|m|l|xxl|\d{1,3}(?:\.\d+)?)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class LineSignature:
    family: str
    key: str
    name: str


@dataclass(frozen=True, slots=True)
class MatchSpec:
    level: Literal["physical"]
    first: Listing
    second: Listing
    status: Literal["pending", "auto_confirmed"]
    confidence: Decimal
    evidence: dict[str, Any]
    relation_type: Literal["relist"] | None = None


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

    async def resolve_run(
        self,
        run_id: int,
        *,
        brand_ids: set[int] | None = None,
        rebuild_all_physical: bool = False,
    ) -> dict[str, int | str]:
        if brand_ids is None:
            brand_ids = {
                brand_id
                for brand_id in await self._session.scalars(
                    select(ParserRunTask.brand_id).where(
                        ParserRunTask.run_id == run_id,
                        ParserRunTask.brand_id.is_not(None),
                    )
                )
                if brand_id is not None
            }
        if not brand_ids:
            return {"version": IDENTITY_VERSION, "listings": 0, "pending": 0, "linked": 0}

        stale = list(
            await self._session.scalars(
                select(Listing)
                .where(
                    Listing.brand_id.in_(brand_ids),
                    or_(
                        Listing.identity_version.is_(None),
                        Listing.identity_version != IDENTITY_VERSION,
                    ),
                )
                .options(
                    load_only(
                        Listing.id,
                        Listing.raw_json,
                        Listing.source_product_id,
                        Listing.source_sku_id,
                        Listing.source_repost_id,
                        Listing.color,
                        Listing.cover_asset_key,
                        Listing.cover_photo_url,
                        Listing.identity_version,
                    )
                )
            )
        )
        await self._backfill(stale)
        await self._session.flush()
        for listing in stale:
            self._session.expire(listing, ["raw_json"])

        listings = list(
            await self._session.scalars(
                select(Listing)
                .where(Listing.brand_id.in_(brand_ids))
                .options(
                    load_only(
                        Listing.id,
                        Listing.source,
                        Listing.grailed_id,
                        Listing.status,
                        Listing.title,
                        Listing.brand_name_raw,
                        Listing.brand_id,
                        Listing.category,
                        Listing.subcategory,
                        Listing.size_raw,
                        Listing.color,
                        Listing.price,
                        Listing.created_at,
                        Listing.sold_at,
                        Listing.updated_at,
                        Listing.first_seen_at,
                        Listing.last_seen_at,
                        Listing.seller_identity,
                        Listing.parser_run_id,
                        Listing.source_product_id,
                        Listing.source_sku_id,
                        Listing.source_repost_id,
                        Listing.cover_asset_key,
                        Listing.cover_photo_url,
                        Listing.cover_content_sha256,
                        Listing.cover_dhash,
                        Listing.identity_version,
                    )
                )
                .order_by(Listing.brand_id, Listing.created_at, Listing.id)
            )
        )
        retired_candidates = await self._retire_stale_candidates()
        await self._assign_models(listings)
        candidate_specs = await self._physical_candidates(
            listings, None if rebuild_all_physical else run_id
        )
        candidates = await self._upsert_matches(candidate_specs)
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
        brand_ids = {listing.brand_id for listing in listings if listing.brand_id is not None}
        brand_names = {
            brand.id: brand.name
            for brand in await self._session.scalars(select(Brand).where(Brand.id.in_(brand_ids)))
        }
        group_rows = list(
            await self._session.scalars(
                select(ModelGroup).where(ModelGroup.brand_id.in_(brand_ids))
            )
        )
        groups = {group.stable_key: group for group in group_rows}
        groups_by_id = {group.id: group for group in group_rows}
        existing = {
            row.listing_id: row
            for row in await self._session.scalars(
                select(ListingModelAssignment)
                .join(Listing, Listing.id == ListingModelAssignment.listing_id)
                .where(Listing.brand_id.in_(brand_ids))
            )
        }
        now = datetime.now(UTC)
        input_hashes = {
            listing.id: compute_input_hash(
                brand=brand_names.get(listing.brand_id, listing.brand_name_raw),
                category=listing.category,
                subcategory=listing.subcategory,
                title=listing.title,
            )
            for listing in listings
            if listing.brand_id is not None
        }
        preserved_ids = {
            listing.id
            for listing in listings
            if (assignment := existing.get(listing.id)) is not None
            and assignment.grouping_version == GROUPING_VERSION
            and assignment.input_hash == input_hashes.get(listing.id)
            and assignment.method.startswith("gemini_")
            and (group := groups_by_id.get(assignment.model_group_id)) is not None
            and group.stable_key.startswith(f"{AI_KEY_PREFIX}:")
        }
        signatures: dict[int, LineSignature | None] = {}
        buckets: dict[tuple[int, str], list[Listing]] = defaultdict(list)
        for listing in listings:
            if listing.brand_id is None or listing.id in preserved_ids:
                continue
            signature = line_signature(
                listing.title,
                brand_names.get(listing.brand_id, listing.brand_name_raw),
                listing.size_raw,
                listing.color,
                listing.category,
            )
            product_type = deterministic_product_type(listing.subcategory)
            if signature is not None and product_type is not None:
                signature = LineSignature(product_type, signature.key, signature.name)
            signatures[listing.id] = signature
            if signature is not None:
                buckets[(listing.brand_id, signature.family)].append(listing)

        decisions: list[tuple[Listing, ModelGroup, str, Decimal]] = []
        for (brand_id, family), bucket in sorted(buckets.items()):
            keys = Counter(
                signature.key for item in bucket if (signature := signatures[item.id]) is not None
            )
            token_sets = {key: frozenset(key.split()) for key in keys}
            keys_by_token: dict[str, set[str]] = defaultdict(set)
            for key, tokens in token_sets.items():
                for token in tokens:
                    keys_by_token[token].add(key)
            coverage = {
                anchor: sum(keys[key] for key in _superset_keys(anchor_tokens, keys_by_token))
                for anchor, anchor_tokens in token_sets.items()
            }
            anchors = {key for key, count in coverage.items() if count >= 2}
            anchors_by_token: dict[str, set[str]] = defaultdict(set)
            fuzzy_anchors: dict[str, set[str]] = defaultdict(set)
            for anchor in anchors:
                for token in token_sets[anchor]:
                    anchors_by_token[token].add(anchor)
                    if len(token) >= 5:
                        for deletion in _deletions(token):
                            fuzzy_anchors[deletion].add(anchor)
            names: dict[str, Counter[str]] = defaultdict(Counter)
            exact_names: dict[str, Counter[str]] = defaultdict(Counter)
            categories: dict[str, Counter[str]] = defaultdict(Counter)
            resolved: list[tuple[Listing, str, str, Decimal]] = []
            for listing in bucket:
                signature = signatures[listing.id]
                assert signature is not None
                subset = {
                    anchor
                    for token in token_sets[signature.key]
                    for anchor in anchors_by_token[token]
                    if token_sets[anchor] <= token_sets[signature.key]
                }
                if subset:
                    anchor = min(
                        subset,
                        key=lambda key: (-coverage[key], len(token_sets[key]), key),
                    )
                    method, confidence = (
                        ("exact_line", Decimal(1))
                        if anchor == signature.key
                        else ("subset_line", Decimal("0.9500"))
                    )
                else:
                    fuzzy_candidates = {
                        candidate
                        for token in token_sets[signature.key]
                        if len(token) >= 5
                        for deletion in _deletions(token)
                        for candidate in fuzzy_anchors[deletion]
                    }
                    fuzzy = [
                        (token_set_ratio(signature.key, candidate), candidate)
                        for candidate in fuzzy_candidates
                        if _fuzzy_line_allowed(signature.key, candidate)
                    ]
                    score, anchor = max(
                        fuzzy,
                        key=lambda item: (item[0], coverage[item[1]], item[1]),
                        default=(0, ""),
                    )
                    if score < 90:
                        anchor = f"listing:{listing.grailed_id}"
                        method, confidence = "unique_listing", Decimal(1)
                    else:
                        method, confidence = "fuzzy_line", Decimal(str(score / 100))
                names[anchor][signature.name] += 1
                if signature.key == anchor:
                    exact_names[anchor][signature.name] += 1
                if listing.category:
                    categories[anchor][listing.category] += 1
                resolved.append((listing, anchor, method, confidence))

            for listing, anchor, method, confidence in resolved:
                stable_key = f"line-v5:{brand_id}:{family}:{anchor}"
                group = groups.get(stable_key)
                fallback_name = listing.title[:255]
                name_counts = exact_names[anchor] or names[anchor]
                chosen_name = (
                    min(
                        name_counts,
                        key=lambda name: (-name_counts[name], len(name), name),
                    )
                    if name_counts
                    else fallback_name
                )
                chosen_category = (
                    min(
                        categories[anchor],
                        key=lambda value: (-categories[anchor][value], value),
                    )
                    if categories[anchor]
                    else listing.category
                )
                if group is None:
                    group = ModelGroup(
                        stable_key=stable_key,
                        brand_id=brand_id,
                        name=chosen_name[:255],
                        category=chosen_category,
                        group_type="resolved",
                        created_at=now,
                        updated_at=now,
                    )
                    self._session.add(group)
                    groups[stable_key] = group
                elif group.name != chosen_name[:255] or group.category != chosen_category:
                    group.name = chosen_name[:255]
                    group.category = chosen_category
                    group.updated_at = now
                decisions.append((listing, group, method, confidence))

        assigned_ids = preserved_ids | {listing.id for listing, *_ in decisions}
        for listing in listings:
            if listing.brand_id is None or listing.id in assigned_ids:
                continue
            family = (
                deterministic_product_type(listing.subcategory)
                or _product_family(model_text(listing.title), listing.category)
                or "unknown"
            )
            stable_key = f"line-v5:{listing.brand_id}:{family}:listing:{listing.grailed_id}"
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
            decisions.append((listing, group, "rule_provisional", Decimal(1)))
        await self._session.flush()
        merge_targets: dict[int, set[int]] = defaultdict(set)
        for listing, group, method, confidence in decisions:
            method = "rule_provisional"
            assignment = existing.get(listing.id)
            if assignment is None:
                assignment = ListingModelAssignment(
                    listing_id=listing.id,
                    model_group_id=group.id,
                    method=method,
                    confidence=confidence,
                    algorithm_version=IDENTITY_VERSION,
                    grouping_version=GROUPING_VERSION,
                    input_hash=input_hashes[listing.id],
                    ai_grouping_run_id=None,
                    updated_at=now,
                )
                self._session.add(assignment)
                existing[listing.id] = assignment
            elif (
                assignment.model_group_id != group.id
                or assignment.method != method
                or assignment.confidence != confidence
                or assignment.algorithm_version != IDENTITY_VERSION
                or assignment.grouping_version != GROUPING_VERSION
                or assignment.input_hash != input_hashes[listing.id]
                or assignment.ai_grouping_run_id is not None
            ):
                if assignment.model_group_id != group.id:
                    merge_targets[assignment.model_group_id].add(group.id)
                assignment.model_group_id = group.id
                assignment.method = method
                assignment.confidence = confidence
                assignment.algorithm_version = IDENTITY_VERSION
                assignment.grouping_version = GROUPING_VERSION
                assignment.input_hash = input_hashes[listing.id]
                assignment.ai_grouping_run_id = None
                assignment.updated_at = now
        for old_group_id, targets in merge_targets.items():
            if len(targets) == 1 and old_group_id not in targets:
                old_group = groups_by_id.get(old_group_id)
                if old_group is not None and not old_group.stable_key.startswith(
                    f"{AI_KEY_PREFIX}:"
                ):
                    old_group.merged_into_id = next(iter(targets))
                    old_group.updated_at = now

    async def _physical_candidates(
        self, listings: Sequence[Listing], run_id: int | None
    ) -> list[MatchSpec]:
        by_seller: dict[str, list[Listing]] = defaultdict(list)
        for listing in listings:
            if listing.seller_identity:
                by_seller[listing.seller_identity].append(listing)
        candidates: list[MatchSpec] = []
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
                if run_id is not None and current.parser_run_id != run_id:
                    continue
                for previous in reversed(ordered[max(0, index - 50) : index]):
                    if previous.brand_id != current.brand_id:
                        continue
                    sold_index = bisect_right(sale_times, _aware(_created(previous)))
                    if sold_index < len(sale_times) and sale_times[sold_index] <= _aware(
                        _created(current)
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
                        status, confidence = "auto_confirmed", Decimal("0.9000")
                    elif (
                        age <= timedelta(days=180)
                        and title >= 80
                        and price_delta <= Decimal("0.30")
                    ):
                        status, confidence = "pending", Decimal("0.7000")
                    if status is None:
                        continue
                    candidates.append(
                        MatchSpec(
                            level="physical",
                            first=previous,
                            second=current,
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
                    )
        return list(
            {tuple(sorted((item.first.id, item.second.id))): item for item in candidates}.values()
        )

    async def _fingerprint_candidates(self, matches: Sequence[IdentityMatch]) -> int:
        if self._transport is None or self._settings.identity_image_requests_per_run == 0:
            return 0
        ids = {
            value for match in matches for value in (match.left_listing_id, match.right_listing_id)
        }
        listings = {
            item.id: item
            for item in await self._session.scalars(
                select(Listing)
                .where(Listing.id.in_(ids))
                .options(
                    load_only(
                        Listing.id,
                        Listing.cover_photo_url,
                        Listing.cover_content_sha256,
                        Listing.cover_dhash,
                    )
                )
            )
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
            for item in await self._session.scalars(
                select(Listing)
                .where(Listing.id.in_(ids))
                .options(
                    load_only(
                        Listing.id,
                        Listing.cover_content_sha256,
                        Listing.cover_dhash,
                    )
                )
            )
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
            if (
                (content_equal or distance is not None and distance <= 4)
                and bool(evidence.get("nonoverlap"))
                and int(evidence.get("title_similarity", 0)) >= 90
                and Decimal(str(evidence.get("price_delta", "1"))) <= Decimal("0.25")
            ):
                match.status = "auto_confirmed"
                match.confidence = Decimal("0.9800")
            elif match.status == "pending":
                match.status = "rejected"
            match.updated_at = datetime.now(UTC)

    async def _upsert_matches(self, specs: Sequence[MatchSpec]) -> list[IdentityMatch]:
        deduplicated: dict[tuple[str, int, int], MatchSpec] = {}
        for spec in specs:
            left, right = sorted((spec.first.id, spec.second.id))
            deduplicated[(spec.level, left, right)] = spec
        if not deduplicated:
            return []

        existing: dict[tuple[str, int, int], IdentityMatch] = {}
        keys = list(deduplicated)
        for offset in range(0, len(keys), 200):
            batch = keys[offset : offset + 200]
            rows = await self._session.scalars(
                select(IdentityMatch).where(
                    tuple_(
                        IdentityMatch.level,
                        IdentityMatch.left_listing_id,
                        IdentityMatch.right_listing_id,
                    ).in_(batch)
                )
            )
            existing.update(
                {(item.level, item.left_listing_id, item.right_listing_id): item for item in rows}
            )

        now = datetime.now(UTC)
        matches: list[IdentityMatch] = []
        for key, spec in deduplicated.items():
            match = existing.get(key)
            if match is None:
                match = IdentityMatch(
                    level=spec.level,
                    left_listing_id=key[1],
                    right_listing_id=key[2],
                    relation_type=spec.relation_type,
                    status=spec.status,
                    confidence=spec.confidence,
                    evidence=spec.evidence,
                    algorithm_version=IDENTITY_VERSION,
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(match)
            elif match.status not in {"confirmed", "rejected"} and (
                match.status != spec.status
                or match.confidence != spec.confidence
                or match.evidence != spec.evidence
                or match.algorithm_version != IDENTITY_VERSION
            ):
                match.status = spec.status
                match.confidence = spec.confidence
                match.evidence = spec.evidence
                match.algorithm_version = IDENTITY_VERSION
                match.updated_at = now
            matches.append(match)
        return matches


def model_text(
    title: str,
    brand: str | None = None,
    size: str | None = None,
    color: str | None = None,
) -> str:
    value = unicodedata.normalize("NFKD", _SIZE.sub(" ", title.casefold()))
    normalized = "".join(char for char in value if not unicodedata.combining(char))
    brand_tokens = {_singularize(token) for token in _TOKEN.findall((brand or "").casefold())}
    variant_tokens = {
        _singularize(token) for token in _TOKEN.findall(f"{size or ''} {color or ''}".casefold())
    }
    raw_tokens = _TOKEN.findall(normalized)
    tokens: list[str] = []
    index = 0
    while index < len(raw_tokens):
        token = _singularize(raw_tokens[index])
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
            and not _SEASON_OR_YEAR.fullmatch(token)
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
    """Compatibility wrapper for the v5 product-line signature."""

    signature = line_signature(title, brand, size, color, category)
    return (signature.family, signature.key) if signature else None


def line_signature(
    title: str,
    brand: str | None = None,
    size: str | None = None,
    color: str | None = None,
    category: str | None = None,
) -> LineSignature | None:
    """Return the normalized family, stable token key and readable line name."""

    canonical = model_text(title, brand, size, color)
    family = _product_family(canonical, category)
    if family is None:
        return None
    ordered = [
        token
        for token in canonical.split()
        if token not in _STOP_WORDS
        and token not in _MODEL_NOISE
        and token not in _FAMILY_TOKENS
        and not _SEASON_OR_YEAR.fullmatch(token)
    ]
    core_tokens = sorted(set(ordered))
    if not any(
        token not in _NON_DISTINCTIVE_MODEL
        and (len(token) >= 3 or any(char.isdigit() for char in token))
        for token in core_tokens
    ):
        return None
    return LineSignature(
        family=family,
        key=" ".join(core_tokens),
        name=" ".join(dict.fromkeys(ordered)).title(),
    )


def model_name(signature: tuple[str, str]) -> str:
    family, core = signature
    return (
        core.title()
        if family in {"accessory", "bottom", "footwear", "top"}
        else (f"{core} {family}".title())
    )


def _product_family(canonical: str, category: str | None) -> str | None:
    tokens = set(canonical.split())
    for family, aliases in _FAMILIES:
        if tokens & aliases:
            return family
    return _CATEGORY_FAMILY.get((category or "").casefold())


def _singularize(token: str) -> str:
    explicit = _TOKEN_NORMALIZATION.get(token)
    if explicit is not None:
        return explicit
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith(("ches", "shes", "xes", "zes", "sses")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _fuzzy_line_allowed(left: str, right: str) -> bool:
    return any(len(token) >= 5 for token in left.split()) and any(
        len(token) >= 5 for token in right.split()
    )


def _deletions(token: str) -> set[str]:
    return {token, *(f"{token[:index]}{token[index + 1 :]}" for index in range(len(token)))}


def _superset_keys(tokens: frozenset[str], keys_by_token: dict[str, set[str]]) -> set[str]:
    candidates = [keys_by_token[token] for token in tokens]
    return set.intersection(*candidates) if candidates else set()


def asset_key(value: str | None) -> str | None:
    if value is None:
        return None
    parts = urlsplit(value)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return None
    canonical = urlunsplit((parts.scheme.casefold(), parts.hostname.casefold(), parts.path, "", ""))
    return hashlib.sha256(canonical.encode()).hexdigest()


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
