"""Golden contracts for YAML normalization and data-quality flags."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.core.config import Settings
from app.domain.listings import ListingData
from app.services.normalization.fx import StaticFxRateProvider
from app.services.normalization.mapping import load_source_mapping, resolve_path
from app.services.normalization.normalizer import ListingNormalizer, NormalizationContext
from app.services.normalization.quality import QualityProcessor
from app.services.sources.base.models import RawHit


async def test_yaml_normalizer_maps_decimal_fx_ms_and_sold_fallback() -> None:
    observed = datetime(2026, 8, 8, 12, tzinfo=UTC)
    payload = {
        "objectID": "42",
        "title": "Archive shoes",
        "description": "Genuine pair",
        "designers": [{"id": 15, "name": "Maison Margiela", "slug": "margiela"}],
        "category": "footwear",
        "category_path": "footwear.sneakers",
        "size": "EU 42",
        "condition": "is_gently_used",
        "price_i": 100,
        "price": 100,
        "sold_price": 90,
        "currency": "EUR",
        "created_at_i": 1_754_568_000_000,
        "sold_at_i": 0,
        "updated_at_i": 1_754_654_400,
        "cover_photo": {"url": "https://img.test/42.jpg"},
        "photo_count": 19,
        "followerno": 23,
        "user": {"id": 7, "username": "must-not-be-stored"},
        "location": "fr",
    }
    rates = StaticFxRateProvider({("EUR", date(2025, 8, 8)): Decimal("1.20")})
    result = await ListingNormalizer(load_source_mapping(), rates).normalize(
        RawHit(payload, "T1"),
        NormalizationContext(status="sold", parser_run_id=1, observed_at=observed),
    )

    assert result.valid
    listing = result.listing
    assert listing is not None
    assert listing.price == Decimal("120.00")
    assert listing.price_original == Decimal("100.00")
    assert listing.sold_price == Decimal("108.00")
    assert listing.fx_rate == Decimal("1.20")
    assert listing.size_normalized == "US 9"
    assert listing.condition == "is_gently_used"
    assert listing.subcategory == "footwear.sneakers"
    assert listing.photo_count == 19
    assert listing.likes_count == 23
    assert listing.schema_version == 2
    assert listing.created_at == datetime(2025, 8, 7, 12, tzinfo=UTC)
    assert listing.sold_at == datetime(2025, 8, 8, 12, tzinfo=UTC)
    assert listing.sold_at_is_estimated
    assert listing.seller_identity is not None and len(listing.seller_identity) == 64
    assert listing.seller_identity_mode == "hashed"
    assert "username" not in listing.raw_json["user"]
    assert "id" not in listing.raw_json["user"]
    assert listing.raw_json["location"] == "FR"


async def test_mapping_rename_is_fixed_only_in_yaml(tmp_path) -> None:  # type: ignore[no-untyped-def]
    mapping_path = tmp_path / "source.yaml"
    mapping_path.write_text(
        """source: grailed
fields:
  grailed_id: [objectID]
  title: [renamed_title]
  brand_name: [designer]
  price_float: [amount]
  currency: [_default:USD]
conditions: {}
""",
        encoding="utf-8",
    )
    result = await ListingNormalizer(load_source_mapping(mapping_path)).normalize(
        RawHit(
            {"objectID": "9", "renamed_title": "Renamed", "designer": "Brand", "amount": "5"},
            "T1",
        ),
        NormalizationContext(
            status="active",
            parser_run_id=1,
            observed_at=datetime(2026, 8, 8, tzinfo=UTC),
        ),
    )

    assert result.listing is not None
    assert result.listing.title == "Renamed"
    assert resolve_path({"photos": [{"url": "a"}, {"url": "b"}]}, "photos[*].url") == [
        "a",
        "b",
    ]


async def test_missing_fx_rate_and_invalid_hits_are_rejected() -> None:
    context = NormalizationContext(
        status="active", parser_run_id=1, observed_at=datetime(2026, 8, 8, tzinfo=UTC)
    )
    normalizer = ListingNormalizer(load_source_mapping())
    invalid = await normalizer.normalize(RawHit({"title": "No id"}, "T1"), context)
    no_rate = await normalizer.normalize(
        RawHit(
            {
                "id": 1,
                "title": "Item",
                "designers": [{"name": "Brand"}],
                "price_i": 100,
                "currency": "EUR",
            },
            "T1",
        ),
        context,
    )

    assert {failure.field for failure in invalid.failures} >= {"grailed_id", "brand_name", "price"}
    assert no_rate.failures[0].code == "missing_fx_rate"


def test_quality_processor_flags_each_supported_case() -> None:
    now = datetime(2026, 8, 8, tzinfo=UTC)
    listings = [
        _listing(1, "Maison Margiela jacket", Decimal("100"), now, photos=1),
        _listing(2, "Maison Margiela jacket", Decimal("105"), now + timedelta(days=1), photos=1),
        _listing(3, "Replica inspired jacket", Decimal("100"), now, photos=1),
        _listing(4, "Lot of jackets read description", Decimal("300"), now, photos=1),
        _listing(5, "Rick Owens jacket", Decimal("100"), now, photos=0),
        _listing(6, "Maison Margiela jacket", Decimal("10000"), now, photos=1),
    ]
    processed = QualityProcessor(Settings()).apply(
        listings,
        known_brand_names={1: ("Maison Margiela",), 2: ("Rick Owens",)},
    )
    flags = {item.grailed_id: set(item.quality_flags) for item in processed}

    assert "repost" not in flags[2]
    assert "possible_replica" in flags[3]
    assert "lot_or_bundle" in flags[4]
    assert {"wrong_brand", "no_photos"}.issubset(flags[5])
    assert "price_outlier" in flags[6]


def _listing(
    identifier: int,
    title: str,
    price: Decimal,
    created_at: datetime,
    *,
    photos: int,
) -> ListingData:
    return ListingData(
        grailed_id=identifier,
        status="active",
        url=f"https://example.test/{identifier}",
        title=title,
        brand_name_raw="Maison Margiela",
        brand_id=1,
        category="outerwear",
        price=price,
        currency_original="USD",
        created_at=created_at,
        first_seen_at=created_at,
        last_seen_at=created_at,
        photo_urls=[f"https://img/{identifier}/{number}" for number in range(photos)],
        photo_count=photos,
        seller_identity="seller-100",
        seller_identity_mode="hashed",
        fetch_tier="T1",
        parser_run_id=1,
        raw_json={"id": identifier},
        schema_version=1,
    )
