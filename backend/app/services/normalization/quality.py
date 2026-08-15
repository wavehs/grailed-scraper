"""Batch data-quality classification for normalized listings."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from statistics import median

from app.core.config import Settings
from app.domain.listings import ListingData

_REPLICA = re.compile(r"\b(rep|replica|inspired|1:1|unauthorized|dhgate)\b", re.I)
_LOT = re.compile(r"\b(bundle|lot of|x\s?\d+|set of|read description)\b", re.I)


class QualityProcessor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def apply(
        self,
        listings: Sequence[ListingData],
        *,
        known_brand_names: Mapping[int, Sequence[str]] | None = None,
    ) -> list[ListingData]:
        known = known_brand_names or {}
        groups: dict[tuple[int | None, str | None], list[ListingData]] = defaultdict(list)
        for listing in listings:
            groups[(listing.brand_id, listing.category)].append(listing)
        medians = {key: median(item.price for item in group) for key, group in groups.items()}
        mads = {
            key: median(abs(item.price - medians[key]) for item in group)
            for key, group in groups.items()
        }
        result: list[ListingData] = []
        for listing in sorted(listings, key=lambda item: item.created_at or item.first_seen_at):
            flags = set(listing.quality_flags)
            text = f"{listing.title}\n{listing.description or ''}"
            if self._settings.quality_filter_replicas and _REPLICA.search(text):
                flags.add("possible_replica")
            group_key = (listing.brand_id, listing.category)
            group_median = medians[group_key]
            group_mad = mads[group_key]
            if _is_outlier(
                listing.price,
                group_median,
                group_mad,
                Decimal(str(self._settings.quality_price_outlier_mad_k)),
            ):
                flags.add("price_outlier")
            if _LOT.search(text) and listing.price >= (
                group_median * Decimal(str(self._settings.quality_lot_price_multiplier))
            ):
                flags.add("lot_or_bundle")
            if listing.photo_count == 0:
                flags.add("no_photos")
            if _wrong_brand(listing, known):
                flags.add("wrong_brand")
            result.append(listing.model_copy(update={"quality_flags": sorted(flags)}))
        return result


def _is_outlier(price: Decimal, center: Decimal, mad: Decimal, k: Decimal) -> bool:
    if mad == 0:
        return price < center * Decimal("0.05") or price > center * Decimal(20)
    return abs(price - center) / (mad * Decimal("1.4826")) > k


def _wrong_brand(listing: ListingData, known: Mapping[int, Sequence[str]]) -> bool:
    title = listing.title.casefold()
    own = {name.casefold() for name in known.get(listing.brand_id or -1, ())}
    if any(name in title for name in own):
        return False
    return any(
        name.casefold() in title
        for brand_id, names in known.items()
        if brand_id != listing.brand_id
        for name in names
    )
