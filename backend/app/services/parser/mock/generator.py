"""Deterministic raw Grailed-shaped data for the offline T0 source."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

DEFAULT_CATALOG_SEED = 20260808
FIXTURE_VERSION = "v1"
ACTIVE_INDEX = "Listing_production"
SOLD_INDEX = "Listing_sold_production"
ACTIVE_SORTED_INDEX = "Listing_by_date_added_production"
SOLD_SORTED_INDEX = "Listing_sold_by_date_production"


@dataclass(frozen=True, slots=True)
class MockBrand:
    """The product's curated seed brands and their Grailed designer names."""

    name: str
    designer_name: str
    aliases: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        normalized = self.designer_name.casefold().replace("'", "")
        return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


BRANDS: tuple[MockBrand, ...] = (
    MockBrand("Chrome Hearts", "Chrome Hearts"),
    MockBrand("Enfants Riches Déprimés", "Enfants Riches Deprimes", ("ERD",)),
    MockBrand("Rick Owens", "Rick Owens", ("RO", "DRKSHDW")),
    MockBrand("Raf Simons", "Raf Simons"),
    MockBrand("Undercover", "Undercover", ("UC",)),
    MockBrand("Number (N)ine", "Number (N)ine", ("Number Nine", "N(N)")),
    MockBrand("Vetements", "Vetements", ("VTMNTS",)),
    MockBrand("Balenciaga", "Balenciaga", ("Bala",)),
    MockBrand("Vivienne Westwood", "Vivienne Westwood", ("VW",)),
    MockBrand("Yohji Yamamoto", "Yohji Yamamoto", ("Yohji", "Y's")),
    MockBrand("Comme des Garçons", "Comme des Garcons", ("CDG",)),
    MockBrand("Stone Island", "Stone Island", ("SI", "SISP")),
    MockBrand("Arc'teryx", "Arc'teryx", ("Arcteryx",)),
    MockBrand("Arc'teryx Veilance", "Arc'teryx Veilance", ("Veilance",)),
    MockBrand("Kapital", "Kapital"),
    MockBrand("Visvim", "Visvim"),
    MockBrand("Carol Christian Poell", "Carol Christian Poell", ("CCP",)),
    MockBrand("Maison Margiela", "Maison Margiela", ("Margiela", "MMM")),
    MockBrand("Bape", "A Bathing Ape", ("BAPE", "AAPE")),
    MockBrand("Hysteric Glamour", "Hysteric Glamour", ("HG",)),
    MockBrand("Jean Paul Gaultier", "Jean Paul Gaultier", ("JPG", "Gaultier")),
)


@dataclass(frozen=True, slots=True)
class MockCatalog:
    """Raw records divided into the two discovery-time Algolia indices."""

    seed: int
    active: tuple[dict[str, Any], ...]
    sold: tuple[dict[str, Any], ...]
    version: str = FIXTURE_VERSION

    @classmethod
    def generate(
        cls,
        *,
        seed: int = DEFAULT_CATALOG_SEED,
        listings_per_status: int = 200,
        brands: tuple[MockBrand, ...] = BRANDS,
    ) -> MockCatalog:
        if listings_per_status < 1:
            raise ValueError("listings_per_status must be positive")
        randomizer = random.Random(seed)
        active: list[dict[str, Any]] = []
        sold: list[dict[str, Any]] = []
        for brand_position, brand in enumerate(brands):
            for item_position in range(listings_per_status):
                active.append(
                    _make_listing(
                        randomizer,
                        brand,
                        brand_position,
                        item_position,
                        "active",
                    )
                )
                sold.append(
                    _make_listing(
                        randomizer,
                        brand,
                        brand_position,
                        item_position,
                        "sold",
                    )
                )
        return cls(seed=seed, active=tuple(active), sold=tuple(sold))

    def records_for_index(self, index_name: str) -> tuple[dict[str, Any], ...]:
        if index_name == ACTIVE_INDEX:
            return self.active
        if index_name == ACTIVE_SORTED_INDEX:
            return tuple(
                sorted(
                    self.active,
                    key=lambda item: (item["created_at_i"], item["id"]),
                    reverse=True,
                )
            )
        if index_name == SOLD_INDEX:
            return self.sold
        if index_name == SOLD_SORTED_INDEX:
            return tuple(
                sorted(self.sold, key=lambda item: (item["sold_at_i"], item["id"]), reverse=True)
            )
        return ()

    def manifest(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "seed": self.seed,
            "brands": len(
                {
                    str(item["designers"][0]["name"])
                    for item in (*self.active, *self.sold)
                }
            ),
            "active_listings": len(self.active),
            "sold_listings": len(self.sold),
        }


def _make_listing(
    randomizer: random.Random,
    brand: MockBrand,
    brand_position: int,
    item_position: int,
    status: Literal["active", "sold"],
) -> dict[str, Any]:
    status_offset = 0 if status == "active" else 10_000_000
    listing_id = status_offset + 1_000_000 + brand_position * 10_000 + item_position
    now = datetime(2026, 8, 8, tzinfo=UTC)
    # Pareto-like tails retain a realistic handful of high-value archive pieces.
    base_cents = 8_000 + brand_position * 1_100
    price_cents = int(Decimal(base_cents) * Decimal(str(randomizer.paretovariate(1.65))))
    price_cents = min(max(price_cents, 3_000), 2_500_000)
    created_at = now - timedelta(days=randomizer.randint(2, 730))
    sold_at = (
        created_at + timedelta(days=randomizer.randint(1, min(180, (now - created_at).days)))
        if status == "sold"
        else None
    )
    category = ("outerwear", "tops", "bottoms", "footwear")[item_position % 4]
    title = f"{brand.designer_name} archive {category} #{item_position + 1}"
    price = Decimal(price_cents) / Decimal(100)
    photo_url = f"https://images.mock.grailed.test/{listing_id}/cover.jpg"
    record: dict[str, Any] = {
        "objectID": str(listing_id),
        "id": listing_id,
        "title": title,
        "description": f"Deterministic fixture listing for {brand.designer_name}.",
        "designers": [{"name": brand.designer_name, "slug": brand.slug}],
        "category": category,
        "category_path": category,
        "size": ("S", "M", "L", "OS")[item_position % 4],
        "condition": ("New/Never worn", "Gently used", "Used")[item_position % 3],
        "price_i": price_cents,
        "price": format(price, ".2f"),
        "currency": "USD",
        "hearts_count": randomizer.randint(0, 450),
        "created_at_i": int(created_at.timestamp()),
        "updated_at_i": int((sold_at or now).timestamp()),
        "cover_photo": {"url": photo_url},
        "photos": [{"url": photo_url}],
        "seller": {"id": 50_000 + brand_position * 1_000 + item_position},
        "location": "US",
        "status": status,
    }
    if sold_at is not None:
        record["sold_at_i"] = int(sold_at.timestamp())
    return record
