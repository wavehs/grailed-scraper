"""grouping-v7 steps 1 and 4: mined groups are derived data with stable ids, and a mined
version belongs only to the line it starts with (docs/GROUPING.md)."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
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
    ModelBlock,
    ModelGroup,
    ParentOverride,
)
from app.db.session import get_db
from app.main import app
from app.services.grouping import GroupingService
from app.services.grouping.mining import MiningSample, mine_phrases, parent_line
from app.services.grouping.policy import GroupingPolicy, load_policy
from app.services.grouping.service import BrandGroupingStats

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


async def _database(
    tmp_path: Path, listings: list[Listing]
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'v7.db'}")
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


def _tees(start: int, title: str, sellers: int, prefix: str) -> list[Listing]:
    return [_listing(start + index, title, f"{prefix}{index}") for index in range(sellers)]


async def _regroup(
    factory: async_sessionmaker[AsyncSession],
    at: datetime,
    *,
    policy: GroupingPolicy | None = None,
    full: bool = True,
) -> BrandGroupingStats:
    async with factory() as session:
        result = await GroupingService(session, policy, clock=lambda: at).regroup(full=full)
        await session.commit()
    return result.brands[0]


async def _group(factory: async_sessionmaker[AsyncSession], slug: str) -> ModelGroup | None:
    async with factory() as session:
        group: ModelGroup | None = await session.scalar(
            select(ModelGroup).where(ModelGroup.slug == slug)
        )
        return group


async def _assigned(factory: async_sessionmaker[AsyncSession], grailed_id: int) -> ModelGroup:
    async with factory() as session:
        group = await session.scalar(
            select(ModelGroup)
            .join(ListingModelAssignment, ListingModelAssignment.model_group_id == ModelGroup.id)
            .join(Listing, Listing.id == ListingModelAssignment.listing_id)
            .where(Listing.grailed_id == grailed_id)
        )
        assert group is not None
        return group


async def _drop(factory: async_sessionmaker[AsyncSession], grailed_ids: list[int]) -> None:
    async with factory() as session:
        await session.execute(delete(Listing).where(Listing.grailed_id.in_(grailed_ids)))
        await session.commit()


async def _add(factory: async_sessionmaker[AsyncSession], listings: list[Listing]) -> None:
    async with factory() as session:
        session.add_all(listings)
        await session.commit()


def _policy(tmp_path: Path, edit: Callable[[Path], None]) -> GroupingPolicy:
    """A copy of the real configuration with one edit (load_policy caches by directory)."""

    directory = tmp_path / "config"
    shutil.copytree(PROJECT_ROOT / "config", directory)
    edit(directory)
    return load_policy(directory)


async def test_stale_group_retires_comes_back_with_its_id_and_expires(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path, _tees(100, "Chrome Hearts Moon Club Tee", 5, "m"))
    first = await _regroup(factory, T0)
    moon = await _group(factory, "moon-club")
    assert first.mined == 1 and moon is not None and moon.status == "auto"

    await _drop(factory, [100, 101, 102])  # two sellers left: below the keep threshold (3)
    stale = await _regroup(factory, T0 + timedelta(days=1))
    retired = await _group(factory, "moon-club")
    assert stale.retired == 1 and retired is not None and retired.id == moon.id
    assert retired.retired_at is not None
    assert (await _assigned(factory, 103)).slug == "_none"

    await _add(factory, _tees(200, "Chrome Hearts Moon Club Tee", 3, "n"))  # five sellers
    back = await _regroup(factory, T0 + timedelta(days=30))
    revived = await _group(factory, "moon-club")
    assert back.revived == 1 and revived is not None and revived.id == moon.id
    assert revived.retired_at is None and (await _assigned(factory, 200)).id == moon.id

    await _drop(factory, [103, 104, 200, 201])
    await _regroup(factory, T0 + timedelta(days=40))
    again = await _regroup(factory, T0 + timedelta(days=60))  # still within the TTL
    assert again.deleted == 0 and await _group(factory, "moon-club") is not None
    expired = await _regroup(factory, T0 + timedelta(days=131))
    assert expired.deleted == 1 and await _group(factory, "moon-club") is None
    await engine.dispose()


async def test_hysteresis_keeps_an_existing_group_but_not_a_new_one(tmp_path: Path) -> None:
    listings = _tees(100, "Chrome Hearts Moon Club Tee", 5, "m")
    listings += _tees(300, "Chrome Hearts Star Gang Tee", 4, "s")  # never reached five
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory, T0)
    moon = await _group(factory, "moon-club")
    assert moon is not None and await _group(factory, "star-gang") is None

    await _drop(factory, [100])  # four sellers: below creation, above the keep threshold
    kept = await _regroup(factory, T0 + timedelta(days=1))
    still = await _group(factory, "moon-club")
    assert kept.retired == 0 and still is not None and still.id == moon.id
    assert still.retired_at is None and await _group(factory, "star-gang") is None
    await engine.dispose()


async def test_a_second_full_pass_changes_nothing(tmp_path: Path) -> None:
    listings = _tees(100, "Chrome Hearts Moon Club Tee", 5, "m")
    listings += _tees(200, "Chrome Hearts Neck Logo Tee", 6, "n")
    listings += _tees(300, "Chrome Hearts Plain Tee", 2, "p")
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory, T0)

    async def state() -> list[tuple[object, ...]]:
        async with factory() as session:
            groups = await session.scalars(select(ModelGroup).order_by(ModelGroup.id))
            return [
                (g.id, g.slug, g.status, g.parent_id, g.retired_at, g.updated_at) for g in groups
            ]

    before = await state()
    second = await _regroup(factory, T0)
    assert second.changed == 0 and second.retired == 0 and second.groups_created == 0
    assert await state() == before
    delta = await _regroup(factory, T0, full=False)
    assert delta.full is False and delta.processed == 0
    await engine.dispose()


async def test_rules_protect_mined_groups_without_confirming_them(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path, _tees(100, "Chrome Hearts Moon Club Tee", 5, "m"))
    await _regroup(factory, T0)
    moon = await _group(factory, "moon-club")
    assert moon is not None
    async with factory() as session:
        listing_id = await session.scalar(select(Listing.id).where(Listing.grailed_id == 104))
        assert listing_id is not None
        session.add(ListingOverride(listing_id=listing_id, model_group_id=moon.id, created_at=T0))
        await session.commit()
    await _drop(factory, [100, 101, 102, 103])
    result = await _regroup(factory, T0 + timedelta(days=200))
    kept = await _group(factory, "moon-club")
    assert result.retired == 0 and result.deleted == 0
    assert kept is not None and kept.status == "auto" and kept.retired_at is None
    assert (await _assigned(factory, 104)).id == moon.id
    await engine.dispose()


async def test_blocked_phrase_is_never_a_model_but_stays_in_titles(tmp_path: Path) -> None:
    listings = _tees(100, "Chrome Hearts Moon Club Tee", 5, "m")
    listings += _tees(200, "Chrome Hearts Moon Tee", 5, "x")
    engine, factory = await _database(tmp_path, listings)
    async with factory() as session:
        session.add(ModelBlock(brand_id=1, phrase="Moon", created_at=T0))
        await session.commit()
    await _regroup(factory, T0)
    assert await _group(factory, "moon") is None  # forbidden as a model…
    club = await _group(factory, "moon-club")
    assert club is not None  # …but "moon" is still a word of "Moon Club"
    assert (await _assigned(factory, 100)).id == club.id
    await engine.dispose()


async def test_respelled_phrase_keeps_the_group_id(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path, _tees(100, "Chrome Hearts X-Pander Tee", 5, "x"))
    await _regroup(factory, T0)
    pander = await _group(factory, "pander")  # "x" is noise in grouping-v6 spelling
    assert pander is not None

    def spell(directory: Path) -> None:
        path = directory / "grouping.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("spelling:\n", "spelling:\n  x pander: xpander\n"), encoding="utf-8"
        )

    respelled = await _regroup(factory, T0 + timedelta(days=1), policy=_policy(tmp_path, spell))
    xpander = await _group(factory, "xpander")
    assert respelled.inherited == 1
    assert xpander is not None and xpander.id == pander.id
    assert "pander" in xpander.aliases and await _group(factory, "pander") is None
    await engine.dispose()


async def test_seed_takes_over_a_mined_group_and_its_id(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path, _tees(100, "Chrome Hearts Moon Club Tee", 5, "m"))
    await _regroup(factory, T0)
    mined = await _group(factory, "moon-club")
    assert mined is not None and mined.status == "auto"

    def seed(directory: Path) -> None:
        path = directory / "models" / "chrome-hearts.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("models:\n", "models:\n  - {name: Moon Club, types: [tshirt]}\n", 1),
            encoding="utf-8",
        )

    result = await _regroup(factory, T0 + timedelta(days=1), policy=_policy(tmp_path, seed))
    confirmed = await _group(factory, "moon-club")
    assert result.promoted == 1
    assert confirmed is not None and confirmed.id == mined.id
    assert (confirmed.status, confirmed.source) == ("confirmed", "seed")
    await engine.dispose()


def test_moving_listings_and_lines_never_confirms_a_group(tmp_path: Path) -> None:
    import asyncio

    listings = _tees(100, "Chrome Hearts Moon Club Tee", 5, "m")
    listings += _tees(200, "Chrome Hearts Neck Logo Tee", 6, "n")
    engine, factory = asyncio.run(_database(tmp_path, listings))
    asyncio.run(_regroup(factory, T0))
    moon = asyncio.run(_group(factory, "moon-club"))
    neck = asyncio.run(_group(factory, "neck-logo"))
    assert moon is not None and neck is not None

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def listing_id(grailed_id: int) -> int:
        async with factory() as session:
            found = await session.scalar(select(Listing.id).where(Listing.grailed_id == grailed_id))
            assert found is not None
            return found

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        moved = client.put(
            f"/api/listings/{asyncio.run(listing_id(200))}/group", json={"group_id": moon.id}
        )
        lined = client.patch(f"/api/groups/{neck.id}", json={"parent_id": moon.id})
        confirmed = client.patch(f"/api/groups/{moon.id}", json={"status": "confirmed"})
    finally:
        app.dependency_overrides.clear()
    assert moved.status_code == 200 and moved.json()["status"] == "auto"
    assert lined.status_code == 200, lined.text
    assert lined.json()["status"] == "auto" and lined.json()["parent_id"] == moon.id
    assert confirmed.json()["status"] == "confirmed"

    async def overrides() -> list[tuple[int, int | None]]:
        async with factory() as session:
            rows = await session.scalars(select(ParentOverride))
            return [(row.group_id, row.parent_id) for row in rows]

    assert asyncio.run(overrides()) == [(neck.id, moon.id)]
    regrouped = asyncio.run(_regroup(factory, T0 + timedelta(days=1)))
    line = asyncio.run(_group(factory, "neck-logo"))
    assert regrouped.retired == 0 and line is not None and line.parent_id == moon.id
    asyncio.run(engine.dispose())


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


async def test_delta_mines_only_phrases_of_new_and_changed_rows(tmp_path: Path) -> None:
    listings = _tees(100, "Chrome Hearts Star Gang Tee", 4, "s")
    listings.append(_listing(104, "Chrome Hearts Star Gang Tee", "s0"))  # fifth listing, 4 sellers
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory, T0)
    assert await _group(factory, "star-gang") is None

    async with factory() as session:  # the seller is not part of the input hash: not "changed"
        await session.execute(
            update(Listing).where(Listing.grailed_id == 104).values(seller_identity="s9")
        )
        await session.commit()
    await _add(factory, [_listing(200, "Chrome Hearts Moon Club Tee", "m0")])
    delta = await _regroup(factory, T0 + timedelta(days=1), full=False)
    assert delta.full is False and delta.processed == 1 and delta.mined == 0
    assert await _group(factory, "star-gang") is None  # five sellers, but not brought by the delta

    await _regroup(factory, T0 + timedelta(days=2))
    assert await _group(factory, "star-gang") is not None  # a full pass sees them
    await engine.dispose()


async def test_settle_keeps_the_retirement_date_and_a_delta_retires_nothing(
    tmp_path: Path,
) -> None:
    engine, factory = await _database(tmp_path, _tees(100, "Chrome Hearts Moon Club Tee", 5, "m"))
    await _regroup(factory, T0)

    await _drop(factory, [100, 101, 102, 103, 104])
    await _add(factory, [_listing(200, "Chrome Hearts Plain Tee", "p0")])  # gives the delta work
    delta = await _regroup(factory, T0 + timedelta(days=1), full=False)
    live = await _group(factory, "moon-club")
    assert delta.processed == 1  # the group is empty, yet only a full pass retires it
    assert delta.retired == 0 and live is not None and live.retired_at is None

    await _regroup(factory, T0 + timedelta(days=2))
    retired = await _group(factory, "moon-club")
    assert retired is not None and retired.retired_at is not None
    await _regroup(factory, T0 + timedelta(days=20))  # still empty: the date must not move
    again = await _group(factory, "moon-club")
    assert again is not None and again.retired_at == retired.retired_at
    await engine.dispose()


def test_split_and_move_into_a_retired_group_are_conflicts(tmp_path: Path) -> None:
    import asyncio

    listings = _tees(100, "Chrome Hearts Moon Club Tee", 5, "m")
    listings += _tees(200, "Chrome Hearts Neck Logo Tee", 6, "n")
    engine, factory = asyncio.run(_database(tmp_path, listings))
    asyncio.run(_regroup(factory, T0))
    asyncio.run(_drop(factory, [100, 101, 102]))
    asyncio.run(_regroup(factory, T0 + timedelta(days=1)))
    moon = asyncio.run(_group(factory, "moon-club"))
    assert moon is not None and moon.retired_at is not None

    async def listing_id(grailed_id: int) -> int:
        async with factory() as session:
            found = await session.scalar(select(Listing.id).where(Listing.grailed_id == grailed_id))
            assert found is not None
            return found

    with _api(factory) as client:
        split = client.post(f"/api/groups/{moon.id}/split", json={"phrase": "Club Tee"})
        moved = client.put(
            f"/api/listings/{asyncio.run(listing_id(200))}/group", json={"group_id": moon.id}
        )
    assert split.status_code == 409 and split.json()["error"]["code"] == "group_ignored"
    assert moved.status_code == 409 and moved.json()["error"]["code"] == "group_scope"
    asyncio.run(engine.dispose())


def test_not_model_for_campaign_keeps_political_campaign(tmp_path: Path) -> None:
    import asyncio

    listings = _tees(100, "Chrome Hearts Political Campaign Tee", 5, "p")
    listings += _tees(200, "Chrome Hearts Campaign Tee", 5, "c")
    engine, factory = asyncio.run(_database(tmp_path, listings))
    asyncio.run(_regroup(factory, T0))
    campaign = asyncio.run(_group(factory, "campaign"))
    political = asyncio.run(_group(factory, "political-campaign"))
    assert campaign is not None and political is not None

    with _api(factory) as client:
        response = client.post(f"/api/groups/{campaign.id}/not-model")
    assert response.status_code == 200, response.text
    asyncio.run(_regroup(factory, T0 + timedelta(days=1)))
    kept = asyncio.run(_group(factory, "political-campaign"))
    forbidden = asyncio.run(_group(factory, "campaign"))
    assert kept is not None and kept.id == political.id and kept.retired_at is None
    assert forbidden is not None and forbidden.status == "ignored"
    assert asyncio.run(_assigned(factory, 100)).id == political.id
    asyncio.run(engine.dispose())


async def test_a_delta_that_adds_seed_groups_becomes_a_pure_full_pass(tmp_path: Path) -> None:
    engine, factory = await _database(tmp_path, _tees(100, "Chrome Hearts Moon Club Tee", 5, "m"))
    await _regroup(factory, T0)
    await _drop(factory, [100, 101, 102])  # two sellers left: below the hysteresis floor
    hat = _listing(300, "Chrome Hearts Plain Hat", "h0")  # a new type brings its seed groups
    hat.category = "accessories"
    hat.subcategory = hat.category_path = "accessories.hats"
    await _add(factory, [hat])

    delta = await _regroup(factory, T0 + timedelta(days=1), full=False)
    assert delta.full is True and delta.retired == 1
    retired = await _group(factory, "moon-club")
    assert retired is not None and retired.retired_at is not None

    after = await _regroup(factory, T0 + timedelta(days=2))
    assert (after.changed, after.retired, after.revived) == (0, 0, 0)
    await engine.dispose()


def test_split_takes_over_the_phrase_of_a_retired_group(tmp_path: Path) -> None:
    import asyncio

    listings = _tees(100, "Chrome Hearts Moon Club Tee", 5, "m")
    listings += _tees(200, "Chrome Hearts Neck Logo Tee", 6, "n")
    engine, factory = asyncio.run(_database(tmp_path, listings))
    asyncio.run(_regroup(factory, T0))
    asyncio.run(_drop(factory, [100, 101, 102]))
    asyncio.run(_regroup(factory, T0 + timedelta(days=1)))
    moon = asyncio.run(_group(factory, "moon-club"))
    neck = asyncio.run(_assigned(factory, 200))
    assert moon is not None and moon.retired_at is not None

    with _api(factory) as client:
        response = client.post(f"/api/groups/{neck.id}/split", json={"phrase": "Moon Club"})
    assert response.status_code == 201, response.text
    taken = asyncio.run(_group(factory, "moon-club"))
    assert taken is not None and taken.id == moon.id
    assert (taken.status, taken.source, taken.retired_at) == ("confirmed", "user", None)
    asyncio.run(engine.dispose())


# Step 4: the line rule.

MODIFIERS = frozenset({"mini", "mega"})


def _lines(*names: str) -> list[tuple[tuple[str, ...], str]]:
    return [(tuple(name.split()), name) for name in names]


def _samples(title: str, sellers: int, prefix: str) -> list[MiningSample]:
    tokens = tuple(title.split())
    return [MiningSample(f"{prefix}{index}", tokens, tokens) for index in range(sellers)]


def test_the_parent_is_the_longest_line_that_starts_the_phrase() -> None:
    lines = _lines("dove", "dove no", "no seam", "robe", "leg")

    assert parent_line(("dove", "no", "seam"), lines, MODIFIERS) == "dove no"
    assert parent_line(("garde", "robe"), lines, MODIFIERS) is None
    assert parent_line(("wide", "leg"), lines, MODIFIERS) is None
    assert parent_line(("no", "seam"), lines, MODIFIERS) is None


def test_leading_protected_modifiers_are_skipped() -> None:
    for lines in (_lines("city", "mini"), _lines("mini", "city")):
        assert parent_line(("mini", "city"), lines, MODIFIERS) == "city"
        assert parent_line(("mega", "mini", "city"), lines, MODIFIERS) == "city"
    assert parent_line(("mega", "mini", "city"), _lines("mini city"), MODIFIERS) == "mini city"
    assert parent_line(("mini",), _lines("mini"), MODIFIERS) is None
    assert parent_line(("city", "mini"), _lines("mini"), MODIFIERS) is None


def test_mining_reports_the_sellers_of_a_phrase_outside_longer_names() -> None:
    samples = _samples("falcon ridge", 12, "r") + _samples("falcon", 5, "f")
    samples += _samples("kestrel wing club", 6, "w") + _samples("kestrel", 10, "k")

    phrases = {item.tokens: item for item in mine_phrases(samples)}

    falcon, kestrel = phrases[("falcon",)], phrases[("kestrel",)]
    assert (falcon.sellers, falcon.own, falcon.fragment) == (17, 5, True)
    assert (kestrel.sellers, kestrel.own, kestrel.fragment) == (16, 10, False)
    assert not phrases[("falcon", "ridge")].fragment


async def test_a_phrase_hangs_under_the_line_it_starts_with(tmp_path: Path) -> None:
    listings = _tees(100, "Chrome Hearts Dove No Seam Tee", 6, "d")
    listings += _tees(200, "Chrome Hearts No Seam Tee", 6, "n")
    listings += _tees(300, "Chrome Hearts Dove Tee", 10, "o")
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory, T0)

    version = await _group(factory, "dove-no-seam")
    dove = await _group(factory, "dove")
    no_seam = await _group(factory, "no-seam")
    assert version is not None and dove is not None and no_seam is not None
    # The old rule took the longest phrase inside: "No Seam", a line of its own.
    assert version.parent_id == dove.id
    assert no_seam.parent_id is None and dove.parent_id is None
    await engine.dispose()


async def test_a_line_inside_the_phrase_is_no_parent(tmp_path: Path) -> None:
    listings = _tees(100, "Chrome Hearts Saturn Kestrel Tee", 6, "s")
    listings += _tees(200, "Chrome Hearts Kestrel Tee", 6, "k")
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory, T0)

    saturn = await _group(factory, "saturn-kestrel")
    kestrel = await _group(factory, "kestrel")
    assert saturn is not None and kestrel is not None
    assert saturn.parent_id is None and kestrel.parent_id is None
    await engine.dispose()


async def test_a_fragment_line_is_no_parent(tmp_path: Path) -> None:
    listings = _tees(100, "Chrome Hearts Falcon Ridge Tee", 12, "r")
    listings += _tees(200, "Chrome Hearts Falcon Tee", 5, "f")
    engine, factory = await _database(tmp_path, listings)
    first = await _regroup(factory, T0)

    ridge = await _group(factory, "falcon-ridge")
    assert ridge is not None and await _group(factory, "falcon") is not None
    assert ridge.parent_id is None
    second = await _regroup(factory, T0 + timedelta(days=1))
    assert (first.mined, second.changed, second.retired) == (2, 0, 0)
    await engine.dispose()


async def test_a_parent_override_still_wins_over_the_line_rule(tmp_path: Path) -> None:
    listings = _tees(100, "Chrome Hearts Saturn Kestrel Tee", 6, "s")
    listings += _tees(200, "Chrome Hearts Kestrel Tee", 6, "k")
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory, T0)
    saturn = await _group(factory, "saturn-kestrel")
    kestrel = await _group(factory, "kestrel")
    assert saturn is not None and kestrel is not None
    async with factory() as session:
        session.add(ParentOverride(group_id=saturn.id, parent_id=kestrel.id, created_at=T0))
        await session.commit()

    await _regroup(factory, T0 + timedelta(days=1))

    line = await _group(factory, "saturn-kestrel")
    assert line is not None and line.parent_id == kestrel.id and line.status == "auto"
    await engine.dispose()
