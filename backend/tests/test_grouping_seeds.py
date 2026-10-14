"""grouping-v7 step 6: a seed of "any" product type gets a group only in the types whose
titles name it, its empty groups elsewhere retire (docs/GROUPING.md §3), and the list of
mined lines a person confirms as models (§6)."""

from __future__ import annotations

import shutil
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
from app.db.models import (
    Base,
    Brand,
    Listing,
    ListingModelAssignment,
    ListingOverride,
    ModelGroup,
)
from app.services.grouping import GroupingService
from app.services.grouping.brands import brand_normalizer
from app.services.grouping.policy import GroupingPolicy, SeedModel, load_policy
from app.services.grouping.reports import LineRow, candidate_lines
from app.services.grouping.seeds import seed_types
from app.services.grouping.service import BrandGroupingStats

T0 = datetime(2026, 10, 1, tzinfo=UTC)
TEE = "tops.short_sleeve_shirts"
HOODIE = "tops.sweatshirts_hoodies"


def _listing(listing_id: int, title: str, category: str = TEE) -> Listing:
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
        subcategory=category,
        category_path=category,
        price=Decimal("100.00"),
        currency_original="USD",
        created_at=created,
        first_seen_at=created,
        last_seen_at=T0,
        photo_urls=[],
        designer_names=["Chrome Hearts"],
        seller_identity=f"seller{listing_id}",
        seller_identity_mode="hashed",
        quality_flags=[],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
    )


async def _database(
    tmp_path: Path, listings: list[Listing]
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'seeds.db'}")
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


def _policy(tmp_path: Path, line: str) -> GroupingPolicy:
    """A copy of the real configuration with one more Chrome Hearts seed."""

    directory = tmp_path / "config"
    shutil.copytree(PROJECT_ROOT / "config", directory)
    path = directory / "models" / "chrome-hearts.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("models:\n", f"models:\n  - {line}\n", 1), encoding="utf-8")
    return load_policy(directory)


async def _regroup(
    factory: async_sessionmaker[AsyncSession],
    at: datetime,
    policy: GroupingPolicy,
    *,
    full: bool = True,
) -> BrandGroupingStats:
    async with factory() as session:
        result = await GroupingService(session, policy, clock=lambda: at).regroup(full=full)
        await session.commit()
    return result.brands[0]


async def _zorba(factory: async_sessionmaker[AsyncSession]) -> dict[str, ModelGroup]:
    """Product type -> the seed group Zorba in it."""

    async with factory() as session:
        groups = await session.scalars(select(ModelGroup).where(ModelGroup.slug == "zorba"))
        return {group.product_type: group for group in groups}


async def _add(factory: async_sessionmaker[AsyncSession], *items: object) -> None:
    async with factory() as session:
        session.add_all(items)
        await session.commit()


def _old_hoodie_group() -> ModelGroup:
    """A group made by an earlier version, which put "any" seeds in every type."""

    return ModelGroup(
        brand_id=1,
        product_type="hoodie",
        slug="zorba",
        name="Zorba",
        aliases=[],
        status="confirmed",
        source="seed",
        created_at=T0,
        updated_at=T0,
    )


def _tee_and_hoodie() -> list[Listing]:
    return [
        _listing(1, "Chrome Hearts Zorba Tee"),
        _listing(2, "Chrome Hearts Plain Hoodie", HOODIE),
    ]


async def test_an_any_type_seed_gets_a_group_only_where_titles_name_it(tmp_path: Path) -> None:
    policy = _policy(tmp_path, "{name: Zorba, types: any}")
    engine, factory = await _database(tmp_path, _tee_and_hoodie())

    await _regroup(factory, T0, policy)

    assert set(await _zorba(factory)) == {"tshirt"}
    await engine.dispose()


async def test_an_empty_any_type_seed_group_retires_and_comes_back_with_its_id(
    tmp_path: Path,
) -> None:
    policy = _policy(tmp_path, "{name: Zorba, types: any}")
    engine, factory = await _database(tmp_path, _tee_and_hoodie())
    await _add(factory, _old_hoodie_group())
    hoodie_id = (await _zorba(factory))["hoodie"].id

    first = await _regroup(factory, T0, policy)
    retired = (await _zorba(factory))["hoodie"]
    again = await _regroup(factory, T0 + timedelta(days=1), policy)
    await _add(factory, _listing(3, "Chrome Hearts Zorba Hoodie", HOODIE))
    back = await _regroup(factory, T0 + timedelta(days=2), policy)
    revived = (await _zorba(factory))["hoodie"]
    steady = await _regroup(factory, T0 + timedelta(days=3), policy)

    assert first.retired == 1 and retired.retired_at is not None
    assert (again.changed, again.retired, again.revived) == (0, 0, 0)
    assert back.revived == 1 and revived.id == hoodie_id and revived.retired_at is None
    assert (steady.changed, steady.retired, steady.revived) == (0, 0, 0)
    await engine.dispose()


async def test_an_any_type_seed_group_a_listing_is_moved_to_stays(tmp_path: Path) -> None:
    policy = _policy(tmp_path, "{name: Zorba, types: any}")
    engine, factory = await _database(tmp_path, _tee_and_hoodie())
    group = _old_hoodie_group()
    await _add(factory, group)
    await _add(factory, ListingOverride(listing_id=2, model_group_id=group.id, created_at=T0))

    result = await _regroup(factory, T0 + timedelta(days=200), policy)

    assert result.retired == 0 and result.deleted == 0
    assert (await _zorba(factory))["hoodie"].retired_at is None
    await engine.dispose()


async def _placement(factory: async_sessionmaker[AsyncSession]) -> tuple[str, str]:
    async with factory() as session:
        row = (
            await session.execute(
                select(ModelGroup.product_type, ModelGroup.slug)
                .join(
                    ListingModelAssignment,
                    ListingModelAssignment.model_group_id == ModelGroup.id,
                )
                .where(ListingModelAssignment.listing_id == 1)
            )
        ).one()
        return row[0], row[1]


async def test_an_upgraded_database_ends_like_a_fresh_one(tmp_path: Path) -> None:
    # "Zorba Top" in a hoodie-or-sweatshirt category: its type comes from category and words,
    # never from the empty Zorba hoodie group an earlier version left behind.
    policy = _policy(tmp_path, "{name: Zorba, types: any}")
    fresh_engine, fresh = await _database(
        tmp_path / "fresh", [_listing(1, "Chrome Hearts Zorba Top", HOODIE)]
    )
    await _regroup(fresh, T0, policy)
    engine, factory = await _database(tmp_path, [_listing(1, "Chrome Hearts Zorba Top", HOODIE)])
    await _add(factory, _old_hoodie_group())

    await _regroup(factory, T0, policy)
    passes = [await _regroup(factory, T0 + timedelta(days=day), policy) for day in (1, 2)]
    live = {kind for kind, group in (await _zorba(factory)).items() if group.retired_at is None}

    assert await _placement(factory) == await _placement(fresh) == ("sweat_top", "zorba")
    assert live == set(await _zorba(fresh)) == {"sweat_top"}
    assert [(item.changed, item.retired, item.revived) for item in passes] == [(0, 0, 0)] * 2
    await engine.dispose()
    await fresh_engine.dispose()


async def test_an_any_type_seed_group_with_a_manual_version_stays(tmp_path: Path) -> None:
    policy = _policy(tmp_path, "{name: Zorba, types: any}")
    engine, factory = await _database(tmp_path, _tee_and_hoodie())
    line = _old_hoodie_group()
    await _add(factory, line)
    version = ModelGroup(
        brand_id=1,
        product_type="hoodie",
        slug="zorba-classic",
        name="Zorba Classic",
        aliases=[],
        parent_id=line.id,
        status="confirmed",
        source="user",
        created_at=T0,
        updated_at=T0,
    )
    await _add(factory, version)

    result = await _regroup(factory, T0 + timedelta(days=200), policy)
    kept = (await _zorba(factory))["hoodie"]

    assert result.retired == 0 and result.deleted == 0 and kept.retired_at is None
    await engine.dispose()


async def test_a_delta_naming_an_any_type_seed_in_a_new_type_becomes_a_full_pass(
    tmp_path: Path,
) -> None:
    policy = _policy(tmp_path, "{name: Zorba, types: any}")
    engine, factory = await _database(tmp_path, _tee_and_hoodie())
    await _regroup(factory, T0, policy)
    quiet = await _regroup(factory, T0 + timedelta(days=1), policy, full=False)
    await _add(factory, _listing(3, "Chrome Hearts Zorba Hoodie", HOODIE))

    delta = await _regroup(factory, T0 + timedelta(days=2), policy, full=False)
    again = await _regroup(factory, T0 + timedelta(days=3), policy, full=False)
    after = await _regroup(factory, T0 + timedelta(days=4), policy)

    assert quiet.processed == 0 and not quiet.full
    assert delta.full and set(await _zorba(factory)) == {"tshirt", "hoodie"}
    assert again.processed == 0 and not again.full
    assert (after.changed, after.retired, after.revived) == (0, 0, 0)
    await engine.dispose()


def test_a_glued_spelling_or_a_typo_names_an_any_type_seed() -> None:
    policy = load_policy()
    brand = Brand(id=1, name="Chrome Hearts", slug="chrome-hearts", aliases=[])
    normalizer = brand_normalizer(policy, brand, ["Chrome Hearts"])
    seeds = (
        SeedModel(name="Garderobe", aliases=("Garderobe",), types=None),
        SeedModel(name="Speedhunters", aliases=("Speedhunters",), types=None),
        SeedModel(name="Fixed", aliases=("Fixed",), types=("tshirt",)),
    )
    titles = [
        ("tshirt", normalizer.normalize("Garde Robe Tee").tokens),
        ("hoodie", normalizer.normalize("Speedhunter Hoodie").tokens),
        ("bag", normalizer.normalize("Fixed Garden Bag").tokens),
    ]

    assert seed_types(seeds, titles, normalizer) == {
        "Garderobe": {"tshirt"},
        "Speedhunters": {"hoodie"},
    }


def test_candidates_rank_mined_lines_with_their_versions_and_merge_partners() -> None:
    lines = [
        LineRow(1, "boots", "strike", "Strike", "auto", None, 40, 20),
        LineRow(2, "boots", "striker", "Striker", "auto", None, 10, 5),
        LineRow(3, "boots", "strike-2", "Strike 2", "auto", 1, 12, 9),
        LineRow(4, "boots", "tractor", "Tractor", "confirmed", None, 90, 60),
        LineRow(5, "boots", "dove", "Dove", "auto", None, 50, 25),
    ]

    rows = candidate_lines(lines, limit=2)

    assert [(row["name"], row["sales"], row["versions"]) for row in rows] == [
        ("Strike", 29, ["Strike 2"]),
        ("Dove", 25, []),
    ]
    assert [(item["slug"], item["reason"]) for item in rows[0]["merge_with"]] == [
        ("striker", "typo")
    ]
    assert rows[0]["seed"] == "{name: Strike, types: [boots]}"


def test_a_line_whose_listings_all_sit_in_versions_is_still_a_candidate() -> None:
    lines = [
        LineRow(1, "boots", "strike", "Strike", "auto", None, 0, 0),
        LineRow(2, "boots", "strike-2", "Strike 2", "auto", 1, 6, 4),
    ]

    rows = candidate_lines(lines)

    assert [(row["name"], row["sales"], row["versions"]) for row in rows] == [
        ("Strike", 4, ["Strike 2"])
    ]
