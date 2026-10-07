"""grouping-v7 step 2: descriptions are groups of their own, brand marks and article codes."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import PROJECT_ROOT
from app.db.models import Base, Brand, Listing, ListingModelAssignment, ModelGroup
from app.db.session import get_db
from app.main import app
from app.services.grouping import GroupingService
from app.services.grouping.descriptors import Descriptor, DescriptorIndex, parse_descriptors
from app.services.grouping.kinds import group_kind
from app.services.grouping.mining import covered
from app.services.grouping.normalize import TitleNormalizer, article_code
from app.services.grouping.policy import GroupingPolicy, load_policy
from app.services.grouping.reports import descriptor_report, seed_conflicts
from app.services.grouping.service import BrandGroupingStats

T0 = datetime(2026, 10, 1, tzinfo=UTC)
TEE = "tops.short_sleeve_shirts"


@pytest.fixture(scope="module")
def policy() -> GroupingPolicy:
    return load_policy()


# --- Dictionary and normalizer ----------------------------------------------------------


def test_every_description_survives_the_normalizer(policy: GroupingPolicy) -> None:
    """A phrase with a color, noise or type word could never match a normalized title."""

    plain = TitleNormalizer(policy)
    dead = [
        " ".join(tokens)
        for descriptor in policy.descriptors
        for tokens in descriptor.phrases
        if plain.phrase(" ".join(tokens)) != tokens
    ]
    assert dead == []
    for slug, marks in policy.brand_descriptors.items():
        branded = TitleNormalizer(policy, brand_slug=slug)
        assert [
            " ".join(tokens)
            for descriptor in marks
            for tokens in descriptor.phrases
            if branded.phrase(" ".join(tokens)) != tokens
        ] == []


def test_seed_names_stay_findable_whatever_the_dictionary_says(policy: GroupingPolicy) -> None:
    """Seeds are the one way to make a description a model: none may vanish or be cut away."""

    wholly_descriptive: set[tuple[str, str]] = set()
    for slug, models in policy.seeds.items():
        normalizer = TitleNormalizer(
            policy, brand_terms=[slug.replace("-", " ")], brand_slug=slug
        )
        index = policy.descriptor_index(slug)
        for model in models:
            for alias in model.aliases:
                tokens = normalizer.phrase(alias)
                assert tokens, f"{slug}: seed {model.name!r} alias {alias!r} normalizes to nothing"
                types = model.types or tuple(policy.taxonomy.types)
                if any(covered(tokens, frozenset(), index.phrases(item)) for item in types):
                    wholly_descriptive.add((slug, model.name))
    # Seeds that are only descriptions are fine (they win over the dictionary), but every one
    # must be a deliberate decision: Stone Island "Nylon Metal", Number (N)ine "High Streets",
    # Balenciaga "Paris" and "Cargo" (sneakers only).
    assert wholly_descriptive <= {
        ("stone-island", "Nylon Metal"),
        ("number-nine", "The High Streets"),
        ("balenciaga", "Paris"),
        ("balenciaga", "Cargo"),
    }, sorted(wholly_descriptive)


def test_chrome_hearts_and_rick_owens_words_are_no_descriptions(policy: GroupingPolicy) -> None:
    """The dictionary is shared by 21 brands: model words of the two big ones must stay clear."""

    for slug, words in {
        "chrome-hearts": ["neck logo", "cross patch", "dagger", "cemetery", "forever", "plus"],
        "rick-owens": ["geobasket", "ramone", "dunk", "tractor", "kiss boot", "bauhaus", "creatch"],
    }.items():
        index = policy.descriptor_index(slug)
        normalizer = TitleNormalizer(policy, brand_slug=slug)
        for value in words:
            tokens = normalizer.phrase(value)
            assert tokens
            assert not any(
                covered(tokens, frozenset(), index.phrases(item)) for item in policy.taxonomy.types
            ), value
    # "neck" alone is deliberately not listed, "mock neck" is a description.
    index = policy.descriptor_index("chrome-hearts")
    assert covered(("neck", "logo"), frozenset(), index.phrases("tshirt")) is False
    assert covered(("mock", "neck"), frozenset(), index.phrases("tshirt")) is True


def test_article_codes_are_dropped_and_model_words_are_not(policy: GroupingPolicy) -> None:
    assert article_code("o1bcso1str0226") and article_code("a1b2c3")
    assert not any(article_code(word) for word in ("3xl", "10xl", "track2", "yzy350", "ss2021"))
    normalizer = TitleNormalizer(policy, brand_terms=["Balenciaga"], brand_slug="balenciaga")
    title = normalizer.normalize("Balenciaga O1BCSO1STR0226 Triple S 3XL 10XL Track 2")
    assert "o1bcso1str0226" not in title.tokens
    assert {"triple", "3xl", "10xl", "track"} <= set(title.tokens)


def test_the_longest_description_wins_and_scopes_apply(policy: GroupingPolicy) -> None:
    index = policy.descriptor_index("balenciaga")

    def name(product_type: str, tokens: tuple[str, ...]) -> str | None:
        match = index.find(product_type, tokens)
        return match.descriptor.name if match else None

    assert name("jeans", ("black", "wide", "leg")) == "Wide Leg"  # not "Wide" nor "Leg"
    assert name("jeans", ("baggy", "wide")) == "Baggy"  # equal length: the leftmost
    assert name("hat", ("leg",)) is None  # "Leg" is a bottoms description
    assert name("sunglasses", ("cat", "eye")) == "Cat Eye"
    assert name("hoodie", ("cat",)) is None  # optics words apply to eyewear only
    assert name("tshirt", ("paris",)) == "Paris"  # a brand mark of Balenciaga
    assert name("tshirt", ("moon", "club")) is None


def test_a_phrase_names_one_description_per_product_type() -> None:
    types = ["jeans", "hat", "tshirt"]
    with pytest.raises(ValueError, match="both"):
        parse_descriptors({"fit": ["baggy"], "style": ["Baggy"]}, types, "test.yaml")
    scoped = parse_descriptors(
        {
            "fit": [{"name": "Leg", "only_types": ["jeans"]}],
            "style": [{"name": "Leg", "only_types": ["hat"]}],
        },
        types,
        "test.yaml",
    )
    assert [item.name for item in scoped] == ["Leg", "Leg"]
    with pytest.raises(ValueError, match="unknown types"):
        parse_descriptors({"fit": [{"name": "Leg", "only_types": ["nope"]}]}, types, "test.yaml")
    with pytest.raises(ValueError, match="unknown descriptor class"):
        parse_descriptors({"vibes": ["calm"]}, types, "test.yaml")


def test_descriptor_slug_and_kind() -> None:
    descriptor = Descriptor("Wide Leg", "fit", (("wide", "leg"), ("wideleg",)))
    assert descriptor.slug == "_desc-wide-leg" and descriptor.aliases == ["wideleg"]
    assert group_kind(descriptor.slug, "jeans") == "descriptor"
    assert DescriptorIndex([descriptor]).find("jeans", ("wideleg",)) is not None


# --- Regrouping --------------------------------------------------------------------------


def _listing(
    listing_id: int,
    title: str,
    seller: str,
    *,
    path: str = TEE,
    brand: str = "Chrome Hearts",
    designers: tuple[str, ...] | None = None,
) -> Listing:
    created = T0 - timedelta(days=10)
    return Listing(
        source="grailed",
        grailed_id=listing_id,
        status="active",
        url=f"https://www.grailed.com/listings/{listing_id}",
        title=title,
        brand_name_raw=brand,
        brand_id=1,
        category=path.partition(".")[0],
        subcategory=path,
        category_path=path,
        price=Decimal("100.00"),
        currency_original="USD",
        created_at=created,
        first_seen_at=created,
        last_seen_at=T0,
        photo_urls=[],
        designer_names=list(designers or (brand,)),
        seller_identity=seller,
        seller_identity_mode="hashed",
        quality_flags=[],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
    )


def _rows(
    start: int,
    title: str,
    sellers: int,
    prefix: str,
    *,
    path: str = TEE,
    brand: str = "Chrome Hearts",
    designers: tuple[str, ...] | None = None,
) -> list[Listing]:
    return [
        _listing(
            start + index, title, f"{prefix}{index}", path=path, brand=brand, designers=designers
        )
        for index in range(sellers)
    ]


async def _database(
    tmp_path: Path, listings: list[Listing], brand: str = "Chrome Hearts"
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'desc.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            Brand(
                id=1,
                name=brand,
                slug=brand.lower().replace(" ", "-"),
                aliases=[],
                include_subbrands=False,
                created_at=T0,
                updated_at=T0,
            )
        )
        session.add_all(listings)
        await session.commit()
    return engine, factory


async def _regroup(
    factory: async_sessionmaker[AsyncSession],
    at: datetime = T0,
    *,
    full: bool = True,
    policy: GroupingPolicy | None = None,
) -> BrandGroupingStats:
    async with factory() as session:
        result = await GroupingService(session, policy, clock=lambda: at).regroup(full=full)
        await session.commit()
    return result.brands[0]


def _policy(tmp_path: Path, edit: Callable[[Path], None]) -> GroupingPolicy:
    """A copy of the real configuration with one edit (load_policy caches by directory)."""

    directory = tmp_path / "config"
    shutil.copytree(PROJECT_ROOT / "config", directory)
    edit(directory)
    return load_policy(directory)


async def _group(
    factory: async_sessionmaker[AsyncSession], slug: str, product_type: str | None = None
) -> ModelGroup | None:
    async with factory() as session:
        statement = select(ModelGroup).where(ModelGroup.slug == slug)
        if product_type:
            statement = statement.where(ModelGroup.product_type == product_type)
        found: ModelGroup | None = await session.scalar(statement)
        return found


async def _placed(
    factory: async_sessionmaker[AsyncSession], grailed_id: int
) -> tuple[ModelGroup, str]:
    async with factory() as session:
        row = (
            await session.execute(
                select(ModelGroup, ListingModelAssignment.method)
                .join(
                    ListingModelAssignment,
                    ListingModelAssignment.model_group_id == ModelGroup.id,
                )
                .join(Listing, Listing.id == ListingModelAssignment.listing_id)
                .where(Listing.grailed_id == grailed_id)
            )
        ).one()
        return row[0], row[1]


async def test_a_description_gets_a_group_and_a_model_still_wins(tmp_path: Path) -> None:
    listings = _rows(100, "Chrome Hearts Moon Club Tee", 5, "m")
    listings += _rows(200, "Chrome Hearts Baggy Tee", 3, "b")
    listings += _rows(300, "Chrome Hearts Tee", 2, "t")
    listings += [_listing(400, "Chrome Hearts Moon Club Baggy Tee", "z")]
    engine, factory = await _database(tmp_path, listings)
    stats = await _regroup(factory)

    described, method = await _placed(factory, 200)
    assert (described.slug, described.name, described.product_type) == (
        "_desc-baggy",
        "Baggy",
        "tshirt",
    )
    assert (described.status, described.source, method) == ("auto", "system", "descriptor")
    assert group_kind(described.slug, described.product_type) == "descriptor"
    assert (await _placed(factory, 300))[0].slug == "_none"
    model, how = await _placed(factory, 400)  # model first, then description
    assert model.slug == "moon-club" and how == "phrase"
    assert (stats.descriptors, stats.no_model) == (3, 2)
    await engine.dispose()


async def test_a_description_is_never_mined_as_a_model(tmp_path: Path) -> None:
    listings = _rows(100, "Chrome Hearts Baggy Tee", 6, "a")
    listings += _rows(200, "Chrome Hearts Mock Neck Tee", 6, "b")
    listings += _rows(300, "Chrome Hearts Neck Logo Tee", 6, "c")
    listings += _rows(
        400, "Chrome Hearts Crew Neck Sweatshirt", 6, "d", path="tops.sweatshirts_hoodies"
    )
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory)

    for slug in ("baggy", "mock-neck", "neck", "crew-neck"):
        assert await _group(factory, slug) is None, slug
    assert await _group(factory, "_desc-baggy") is not None
    assert await _group(factory, "_desc-mock-neck") is not None
    neck_logo = await _group(factory, "neck-logo")  # the words stay in titles: a real model
    assert neck_logo is not None and neck_logo.status == "auto" and neck_logo.source == "mined"
    assert (await _placed(factory, 400))[0].slug == "_none"  # "crew neck" is a type word
    await engine.dispose()


async def test_a_phrase_made_of_two_descriptions_is_not_a_model(tmp_path: Path) -> None:
    """"High Rise" + "Wide Leg" must not give the model "Rise Wide Leg"."""

    engine, factory = await _database(
        tmp_path, _rows(100, "Chrome Hearts High Rise Wide Leg Tee", 6, "a")
    )
    await _regroup(factory)
    assert await _group(factory, "rise-wide-leg") is None
    assert await _group(factory, "rise") is None
    assert (await _placed(factory, 100))[0].slug == "_desc-high-rise"  # equal length: leftmost
    await engine.dispose()


def test_a_brand_mark_cannot_repeat_a_dictionary_phrase(tmp_path: Path) -> None:
    def repeat_logo(directory: Path) -> None:
        path = directory / "models" / "balenciaga.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("  - poetic\n", "  - poetic\n  - logo\n"), encoding="utf-8")

    with pytest.raises(ValueError, match="balenciaga.yaml: 'logo' is listed for both"):
        _policy(tmp_path, repeat_logo)


def _wholesale(directory: Path) -> None:
    path = directory / "grouping.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("  retire_ttl_days: 90\n", "  retire_ttl_days: 90\n  wholesale_listings: 3\n"),
        encoding="utf-8",
    )


async def test_a_wholesaler_does_not_count_as_a_seller_when_switched_on(
    tmp_path: Path,
) -> None:
    def listings() -> list[Listing]:
        return _rows(100, "Chrome Hearts Moon Club Tee", 4, "m") + [
            _listing(200, "Chrome Hearts Moon Club Tee", "bulk"),
            _listing(201, "Chrome Hearts Tee", "bulk"),
            _listing(202, "Chrome Hearts Tee", "bulk"),
        ]

    engine, factory = await _database(tmp_path, listings())
    await _regroup(factory)  # off by default: five sellers make the line
    assert await _group(factory, "moon-club") is not None
    await engine.dispose()

    (tmp_path / "bulk").mkdir()
    engine, factory = await _database(tmp_path / "bulk", listings())
    await _regroup(factory, policy=_policy(tmp_path, _wholesale))
    assert await _group(factory, "moon-club") is None  # four sellers left
    assert (await _placed(factory, 200))[0].slug == "_none"
    await engine.dispose()


def _without_descriptions(directory: Path) -> None:
    path = directory / "grouping.yaml"
    text = path.read_text(encoding="utf-8")
    start, end = text.index("\ndescriptors:"), text.index("\n# Colors;")
    path.write_text(text[:start] + text[end:], encoding="utf-8")


async def test_a_forbidden_name_is_not_inherited_as_an_alias(tmp_path: Path) -> None:
    """A group that was "Wide Leg" must not carry the now forbidden phrase into a new model."""

    old_rules = _policy(tmp_path, _without_descriptions)
    engine, factory = await _database(tmp_path, _rows(100, "Chrome Hearts Wide Leg Tee", 6, "w"))
    await _regroup(factory, T0, policy=old_rules)
    old = await _group(factory, "wide-leg")
    assert old is not None  # before the dictionary "Wide Leg" is a mined model

    async with factory() as session:
        for listing in await session.scalars(select(Listing)):
            listing.title = "Chrome Hearts Rise Wide Leg Tee"
        await session.commit()
    after = await _regroup(factory, T0 + timedelta(days=1))
    mined = await _group(factory, "rise-wide-leg")
    assert mined is not None and mined.id != old.id and "wide leg" not in mined.aliases
    assert after.inherited == 0

    again = await _regroup(factory, T0 + timedelta(days=2))
    assert (again.changed, again.retired, again.revived, again.groups_created) == (0, 0, 0, 0)
    await engine.dispose()


async def test_a_seed_taking_a_mined_group_does_not_keep_its_old_line_alive(
    tmp_path: Path,
) -> None:
    """"Mud Show" was once a version of the mined line "Mud"; as a seed it owns its parent."""

    def add_seed(directory: Path) -> None:
        path = directory / "models" / "chrome-hearts.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "models:\n", "models:\n  - {name: Moon Club, types: [tshirt]}\n", 1
            ),
            encoding="utf-8",
        )

    seeded = _policy(tmp_path, add_seed)
    engine, factory = await _database(tmp_path, _rows(100, "Chrome Hearts Moon Club Tee", 6, "m"))
    async with factory() as session:
        line = ModelGroup(
            brand_id=1, product_type="tshirt", slug="moon", name="Moon", aliases=["moon"],
            status="auto", source="mined", created_at=T0, updated_at=T0,
        )
        session.add(line)
        await session.flush()
        session.add(
            ModelGroup(
                brand_id=1, product_type="tshirt", slug="moon-club", name="Moon Club",
                aliases=["moon club"], parent_id=line.id, status="auto", source="mined",
                created_at=T0, updated_at=T0,
            )
        )
        await session.commit()
    await _regroup(factory, T0, policy=seeded)

    club = await _group(factory, "moon-club")
    moon = await _group(factory, "moon")
    assert club is not None and (club.status, club.source, club.parent_id) == (
        "confirmed", "seed", None
    )
    assert moon is not None and moon.retired_at is not None  # nothing protects it any more
    again = await _regroup(factory, T0 + timedelta(days=1), policy=seeded)
    assert (again.changed, again.retired, again.revived, again.groups_created) == (0, 0, 0, 0)
    await engine.dispose()


async def test_descriptions_change_nothing_on_a_second_pass_or_a_delta(tmp_path: Path) -> None:
    listings = _rows(100, "Chrome Hearts Baggy Tee", 3, "b")
    listings += _rows(200, "Chrome Hearts Tee", 2, "t")
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory)
    again = await _regroup(factory, T0 + timedelta(days=1))
    assert (again.changed, again.retired, again.revived, again.groups_created) == (0, 0, 0, 0)
    delta = await _regroup(factory, T0 + timedelta(days=2), full=False)
    assert delta.full is False and delta.processed == 0
    async with factory() as session:
        group = await session.scalar(select(ModelGroup).where(ModelGroup.slug == "_desc-baggy"))
        assert group is not None and group.updated_at.replace(tzinfo=UTC) == T0
    await engine.dispose()


async def test_a_description_group_retires_with_its_titles_and_returns_with_its_id(
    tmp_path: Path,
) -> None:
    engine, factory = await _database(tmp_path, _rows(100, "Chrome Hearts Baggy Tee", 2, "b"))
    await _regroup(factory)
    baggy = await _group(factory, "_desc-baggy")
    assert baggy is not None

    async with factory() as session:
        await session.execute(delete(Listing).where(Listing.grailed_id.in_([100, 101])))
        await session.commit()
    gone = await _regroup(factory, T0 + timedelta(days=1))
    retired = await _group(factory, "_desc-baggy")
    assert gone.retired == 1 and retired is not None and retired.retired_at is not None

    async with factory() as session:
        session.add_all(_rows(200, "Chrome Hearts Baggy Tee", 1, "n"))
        await session.commit()
    back = await _regroup(factory, T0 + timedelta(days=2))
    revived = await _group(factory, "_desc-baggy")
    assert back.revived == 1 and revived is not None and revived.id == baggy.id
    assert revived.retired_at is None
    await engine.dispose()


async def test_paris_is_a_model_for_sneakers_and_a_brand_mark_everywhere_else(
    tmp_path: Path,
) -> None:
    listings = _rows(
        100, "Balenciaga Paris Sneakers", 2, "s", path="footwear.lowtop_sneakers",
        brand="Balenciaga",
    )
    listings += _rows(200, "Balenciaga Paris Tee", 6, "t", brand="Balenciaga")
    engine, factory = await _database(tmp_path, listings, brand="Balenciaga")
    await _regroup(factory)

    sneaker, _ = await _placed(factory, 100)
    assert (sneaker.slug, sneaker.status, sneaker.source) == ("paris", "confirmed", "seed")
    assert await _group(factory, "paris", "tshirt") is None  # never mined: a brand mark
    mark, method = await _placed(factory, 200)
    assert (mark.slug, mark.product_type, method) == ("_desc-paris", "tshirt", "descriptor")
    await engine.dispose()


@contextmanager
def _api(factory: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_a_description_group_cannot_be_edited(tmp_path: Path) -> None:
    engine, factory = asyncio.run(
        _database(tmp_path, _rows(100, "Chrome Hearts Baggy Tee", 2, "b"))
    )
    asyncio.run(_regroup(factory))
    baggy = asyncio.run(_group(factory, "_desc-baggy"))
    assert baggy is not None
    with _api(factory) as client:
        response = client.post(f"/api/groups/{baggy.id}/not-model")
        renamed = client.patch(f"/api/groups/{baggy.id}", json={"name": "Loose"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "service_group"
    assert renamed.status_code == 409
    asyncio.run(engine.dispose())


def test_a_line_split_out_of_a_description_has_no_parent(tmp_path: Path) -> None:
    engine, factory = asyncio.run(
        _database(tmp_path, _rows(100, "Chrome Hearts Baggy Tee", 2, "b"))
    )
    asyncio.run(_regroup(factory))
    baggy = asyncio.run(_group(factory, "_desc-baggy"))
    assert baggy is not None
    with _api(factory) as client:
        response = client.post(
            f"/api/groups/{baggy.id}/split", json={"phrase": "Baggy", "as_version": True}
        )
    assert response.status_code == 201, response.text
    line = asyncio.run(_group(factory, response.json()["slug"], "tshirt"))
    assert line is not None and line.parent_id is None
    asyncio.run(engine.dispose())


def test_seed_conflicts_name_the_models_made_of_descriptions(policy: GroupingPolicy) -> None:
    rows = seed_conflicts(policy, "balenciaga")
    paris = [row for row in rows if row["seed"] == "Paris"]
    assert paris and set(paris[0]["described_in"]) == {"lowtop_sneakers", "hitop_sneakers"}
    assert "Triple S" not in {row["seed"] for row in rows}
    assert all("seed" in row and "alias" in row for row in rows)


async def test_the_report_lists_what_the_dictionary_cut_off_from_mining(tmp_path: Path) -> None:
    listings = _rows(100, "Chrome Hearts Mock Neck Tee", 6, "a")
    listings += _rows(200, "Chrome Hearts Neck Logo Tee", 6, "b")
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory)
    async with factory() as session:
        brand = await session.get(Brand, 1)
        assert brand is not None
        report = await descriptor_report(session, load_policy(), brand)
    cut = {row["phrase"]: row for row in report["suppressed_candidates"]}
    assert set(cut) == {"Mock Neck"}  # "Neck Logo" is still a model
    assert cut["Mock Neck"]["listings"] == 6 and cut["Mock Neck"]["sellers"] == 6
    (group,) = [row for row in report["descriptor_groups"] if row["name"] == "Mock Neck"]
    assert (group["product_type"], group["class"], group["listings"]) == ("tshirt", "detail", 6)
    await engine.dispose()


def test_split_removes_the_designers_like_regrouping_does(tmp_path: Path) -> None:
    """The collaboration's name ("Gap") is a brand term in regrouping, so it is one in split."""

    listings = _rows(
        100, "Chrome Hearts Neck Logo Tee", 6, "n", designers=("Chrome Hearts", "Gap")
    )
    engine, factory = asyncio.run(_database(tmp_path, listings))
    asyncio.run(_regroup(factory))
    neck = asyncio.run(_group(factory, "neck-logo"))
    assert neck is not None
    with _api(factory) as client:
        response = client.post(f"/api/groups/{neck.id}/split", json={"phrase": "Gap Star Gang"})
    assert response.status_code == 201, response.text
    assert response.json()["slug"] == "star-gang"
    asyncio.run(engine.dispose())
