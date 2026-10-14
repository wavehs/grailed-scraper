"""grouping-v7 step 5: one model, many spellings ("X-Pander", "Super Busy"/"Superbusy",
aliases added to the seed file) and the list of groups a person may merge (docs/GROUPING.md)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import PROJECT_ROOT
from app.db.models import Base, Brand, Listing, ListingModelAssignment, ModelGroup
from app.services.grouping import GroupingService
from app.services.grouping.assign import ModelDictionary, ModelEntry
from app.services.grouping.mining import MiningSample, mine_phrases
from app.services.grouping.policy import GroupingPolicy, load_policy
from app.services.grouping.reports import LineRow, merge_pairs
from app.services.grouping.service import BrandGroupingStats
from app.services.grouping.text import phrase, words

T0 = datetime(2026, 10, 1, tzinfo=UTC)
TEE = "tops.short_sleeve_shirts"


def _listing(listing_id: int, title: str, seller: str) -> Listing:
    created = T0 - timedelta(days=10)
    return Listing(
        source="grailed",
        grailed_id=listing_id,
        status="active",
        url=f"https://www.grailed.com/listings/{listing_id}",
        title=title,
        brand_name_raw="Chrome Hearts",
        brand_id=1,
        category="tops",
        subcategory=TEE,
        category_path=TEE,
        price=Decimal("100.00"),
        currency_original="USD",
        created_at=created,
        first_seen_at=created,
        last_seen_at=T0,
        photo_urls=[],
        designer_names=["Chrome Hearts"],
        seller_identity=seller,
        seller_identity_mode="hashed",
        quality_flags=[],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
    )


def _tees(start: int, title: str, sellers: int, prefix: str) -> list[Listing]:
    return [_listing(start + index, title, f"{prefix}{index}") for index in range(sellers)]


async def _database(
    tmp_path: Path, listings: list[Listing]
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'spellings.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            Brand(
                id=1,
                name="Chrome Hearts",
                slug="chrome-hearts",
                aliases=["CH"],
                include_subbrands=False,
                created_at=T0,
                updated_at=T0,
            )
        )
        session.add_all(listings)
        await session.commit()
    return engine, factory


async def _regroup(
    factory: async_sessionmaker[AsyncSession], at: datetime, policy: GroupingPolicy | None = None
) -> BrandGroupingStats:
    async with factory() as session:
        result = await GroupingService(session, policy, clock=lambda: at).regroup(full=True)
        await session.commit()
    return result.brands[0]


async def _placed(factory: async_sessionmaker[AsyncSession]) -> dict[int, ModelGroup]:
    """Grailed id -> group of every listing."""

    async with factory() as session:
        rows = await session.execute(
            select(Listing.grailed_id, ModelGroup)
            .join(ListingModelAssignment, ListingModelAssignment.listing_id == Listing.id)
            .join(ModelGroup, ModelGroup.id == ListingModelAssignment.model_group_id)
        )
        return {grailed_id: group for grailed_id, group in rows.tuples()}


def _policy(tmp_path: Path, edit: Callable[[Path], None], name: str) -> GroupingPolicy:
    """A copy of the real configuration with one edit (load_policy caches by directory)."""

    directory = tmp_path / name
    shutil.copytree(PROJECT_ROOT / "config", directory)
    edit(directory)
    return load_policy(directory)


def _seed(line: str) -> Callable[[Path], None]:
    def edit(directory: Path) -> None:
        path = directory / "models" / "chrome-hearts.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("models:\n", f"models:\n  - {line}\n", 1), encoding="utf-8")

    return edit


# --- one letter and a hyphen ------------------------------------------------------------


def test_one_letter_and_a_hyphen_make_one_word() -> None:
    assert [(word.norm, word.surface) for word in words("X-Pander V-Neck")] == [
        ("xpander", "X-Pander"),
        ("vneck", "V-Neck"),
    ]
    assert phrase("X-Pander") == phrase("Xpander") == ("xpander",)
    assert phrase("T-Shirts") == ("tshirt",)


def test_other_hyphens_still_split_words() -> None:
    assert phrase("A-Cold-Wall") == ("a", "cold", "wall")
    assert phrase("Short-sleeve") == ("short", "sleeve")
    assert phrase("X-Ray2") == ("x", "ray", "2")
    assert phrase("Garde-Robe") == ("garde", "robe")


# --- written as one word or apart --------------------------------------------------------


def _entry(key: int, name: str, status: str) -> ModelEntry:
    return ModelEntry(key, "bag", name, (phrase(name),), status=status)


def test_two_words_glue_into_a_confirmed_one_word_model() -> None:
    dictionary = ModelDictionary([_entry(1, "Hourglass", "confirmed"), _entry(2, "Glass", "auto")])

    match = dictionary.match("bag", ("hour", "glass", "mini"))

    assert match is not None and match.entry.key == 1


def test_mined_one_word_models_do_not_glue() -> None:
    dictionary = ModelDictionary([_entry(1, "Superbusy", "auto"), _entry(2, "Busy", "auto")])

    match = dictionary.match("bag", ("super", "busy"))

    assert match is not None and match.entry.key == 2


def test_a_glued_word_never_beats_a_longer_phrase_as_written() -> None:
    dictionary = ModelDictionary(
        [_entry(1, "Le Cagole Mini", "confirmed"), _entry(2, "Cagolemini", "confirmed")]
    )

    match = dictionary.match("bag", ("le", "cagole", "mini"))

    assert match is not None and match.entry.key == 1


def _samples(text: str, sellers: int, prefix: str) -> list[MiningSample]:
    tokens = phrase(text)
    return [MiningSample(f"{prefix}{index}", tokens, tokens) for index in range(sellers)]


def test_a_confirmed_one_word_name_written_apart_is_not_mined_nor_its_parts() -> None:
    samples = _samples("super busy", 6, "s")

    found = mine_phrases(samples, excluded=[("superbusy",)], solid=frozenset({"superbusy"}))
    unconfirmed = mine_phrases(samples, excluded=[("superbusy",)])

    assert [item.tokens for item in found] == []
    assert [item.tokens for item in unconfirmed] == [("super", "busy")]


def test_both_spellings_mined_become_one_model_with_a_variant() -> None:
    samples = [*_samples("super busy", 6, "s"), *_samples("superbusy", 5, "j")]

    found = mine_phrases(samples)

    assert [(item.tokens, item.variants) for item in found] == [
        (("super", "busy"), (("superbusy",),))
    ]
    assert found[0].sellers == 11 and found[0].listings == 11


def test_the_spelling_with_more_sellers_wins_and_a_tie_keeps_the_group() -> None:
    samples = [*_samples("super busy", 5, "s"), *_samples("superbusy", 5, "j")]

    assert [item.tokens for item in mine_phrases(samples)] == [("super", "busy")]
    kept = mine_phrases(samples, keep=[("superbusy",)], keep_min_sellers=3)
    assert [(item.tokens, item.variants) for item in kept] == [
        (("superbusy",), (("super", "busy"),))
    ]


def test_a_rare_spelling_apart_joins_the_one_word_name() -> None:
    samples = [*_samples("superbusy", 6, "j"), *_samples("super busy", 2, "s")]

    found = mine_phrases(samples)

    assert [(item.tokens, item.variants, item.sellers) for item in found] == [
        (("superbusy",), (("super", "busy"),), 8)
    ]


async def test_both_spellings_share_one_group_across_passes(tmp_path: Path) -> None:
    engine, factory = await _database(
        tmp_path,
        [
            *_tees(100, "Chrome Hearts Super Busy Tee", 6, "s"),
            *_tees(200, "Chrome Hearts Superbusy Tee", 5, "j"),
        ],
    )
    first = await _regroup(factory, T0)
    placed = await _placed(factory)
    groups = {group.slug for group in placed.values()}
    again = await _regroup(factory, T0 + timedelta(days=1))

    assert groups == {"super-busy"}
    assert "superbusy" in placed[200].aliases
    assert first.mined == 1 and again.changed == 0
    await engine.dispose()


# --- aliases of the seed file ------------------------------------------------------------


async def test_aliases_added_to_a_seed_reach_its_existing_group(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path, _tees(100, "Chrome Hearts Zorbo Tee", 2, "z"))
    before = _policy(tmp_path, _seed("{name: Zorba, types: [tshirt]}"), "before")
    await _regroup(factory, T0, before)
    assert (await _placed(factory))[100].slug == "_none"

    after = _policy(tmp_path, _seed("{name: Zorba, aliases: [zorbo], types: [tshirt]}"), "after")
    result = await _regroup(factory, T0 + timedelta(days=1), after)
    placed = await _placed(factory)

    assert result.seed_aliases == 1
    assert placed[100].slug == "zorba" and placed[100].aliases == ["zorbo"]
    assert (await _regroup(factory, T0 + timedelta(days=2), after)).seed_aliases == 0
    await engine.dispose()


async def test_aliases_added_to_an_any_type_seed_place_listings_at_once(tmp_path: Path) -> None:
    # "Zorba Tee" gives the "any" seed its T-shirt group (step 6); "Zorbo" is its new alias.
    engine, factory = await _database(
        tmp_path,
        [
            *_tees(100, "Chrome Hearts Zorbo Tee", 2, "z"),
            *_tees(300, "Chrome Hearts Zorba Tee", 1, "a"),
        ],
    )
    await _regroup(factory, T0, _policy(tmp_path, _seed("{name: Zorba, types: any}"), "before"))
    after = _policy(tmp_path, _seed("{name: Zorba, aliases: [zorbo], types: any}"), "after")

    result = await _regroup(factory, T0 + timedelta(days=1), after)
    placed = await _placed(factory)
    again = await _regroup(factory, T0 + timedelta(days=2), after)

    assert result.seed_aliases == 1 and result.full
    assert placed[100].slug == "zorba"
    assert again.changed == 0
    await engine.dispose()


async def test_seed_alias_sync_keeps_manual_aliases(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path, _tees(100, "Chrome Hearts Zorbo Tee", 2, "z"))
    policy = _policy(tmp_path, _seed("{name: Zorba, aliases: [zorbo], types: [tshirt]}"), "p")
    await _regroup(factory, T0, policy)
    async with factory() as session:
        group = await session.scalar(select(ModelGroup).where(ModelGroup.slug == "zorba"))
        assert group is not None
        group.aliases = ["zorba classic"]
        await session.commit()

    await _regroup(factory, T0 + timedelta(days=1), policy)
    placed = await _placed(factory)

    assert placed[100].aliases == ["zorba classic", "zorbo"]
    await engine.dispose()


# --- groups a person may merge -----------------------------------------------------------


def _line(group_id: int, slug: str, sales: int, parent: int | None = None) -> LineRow:
    return LineRow(group_id, "boots", slug, slug.title(), "auto", parent, sales * 2, sales)


def test_merge_candidates_list_typos_and_spellings_not_short_names_or_versions() -> None:
    lines = [
        _line(1, "strike", 50),
        _line(2, "striker", 20),
        _line(3, "cash", 9),
        _line(4, "case", 9),
        _line(5, "garde-robe", 7),
        _line(6, "garderobe", 1),
        _line(7, "strike-2", 4, parent=1),
        _line(8, "track-2", 30),
        _line(9, "track-3", 30),
    ]

    pairs = merge_pairs(lines)

    assert [
        (pair["reason"], [item["slug"] for item in pair["groups"]], pair["sales"])
        for pair in pairs
    ] == [("typo", ["strike", "striker"], 70), ("spelling", ["garde-robe", "garderobe"], 8)]
