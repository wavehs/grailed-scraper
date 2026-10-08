"""grouping-v7 step 3: whitelisted collaborations get a line of their own, `kind` in the API."""

from __future__ import annotations

import asyncio
import csv
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
from app.services.grouping import GroupingService, evaluation
from app.services.grouping.brands import brand_normalizer
from app.services.grouping.collabs import parse_collabs
from app.services.grouping.kinds import collab_partner, group_kind
from app.services.grouping.policy import GroupingPolicy, load_policy
from app.services.grouping.reports import collab_report
from app.services.grouping.service import BrandGroupingStats
from app.services.grouping.text import fold
from app.services.metrics import MetricsService

T0 = datetime(2026, 10, 1, tzinfo=UTC)
HOODIES = "tops.sweatshirts_hoodies"
SWEATPANTS = "bottoms.sweatpants_joggers"
YEEZY = "_collab-yeezy-gap"


@pytest.fixture(scope="module")
def policy() -> GroupingPolicy:
    return load_policy()


# --- Whitelist ---------------------------------------------------------------------------


def test_the_whitelist_is_the_collab_class_of_the_codesigners_list(policy: GroupingPolicy) -> None:
    path = PROJECT_ROOT / "eval" / "balenciaga" / "codesigners.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["class"] == "collab"]
    expected: dict[str, set[str]] = {}
    for row in rows:
        expected.setdefault(row["partner"], set()).add(fold(row["designer"]))
    collabs = {collab.name: collab for collab in policy.collabs["balenciaga"]}
    assert set(collabs) == set(expected)
    for name, designers in expected.items():
        assert designers <= collabs[name].designers, name
    yeezy = collabs["Yeezy Gap"]
    assert yeezy.designers == {"gap", "yeezy", "yeezy gap", "kanye west"}
    assert set(yeezy.aliases) == {"yeezy gap", "ygebb", "yzy gap", "engineered by balenciaga"}


def test_a_collab_slug_round_trips_with_the_eval_partner_key(policy: GroupingPolicy) -> None:
    for collab in policy.collabs["balenciaga"]:
        assert group_kind(collab.slug, "hoodie") == "collab"
        assert collab_partner(collab.slug) == evaluation._partner_key(collab.name), collab.name
    by_name = {collab.name: collab.slug for collab in policy.collabs["balenciaga"]}
    assert by_name["Yeezy Gap"] == YEEZY
    assert by_name["10 Corso Como"] == "_collab-10-corso-como"
    assert by_name["The Simpsons"] == "_collab-the-simpsons"


def test_a_phrase_names_one_collaboration() -> None:
    with pytest.raises(ValueError, match="both"):
        parse_collabs(
            [{"name": "Yeezy Gap", "aliases": ["ygebb"]}, {"name": "Gap", "aliases": ["YGEBB"]}],
            "test.yaml",
        )
    with pytest.raises(ValueError, match="no words"):
        parse_collabs([{"name": "!!"}], "test.yaml")
    (plain,) = parse_collabs(["Crocs"], "test.yaml")
    assert (plain.name, plain.designers, plain.slug) == (
        "Crocs",
        frozenset({"crocs"}),
        "_collab-crocs",
    )


def test_collaboration_names_are_brand_terms(policy: GroupingPolicy) -> None:
    """"Engineered by Balenciaga" leaves no "engineered" behind for mining."""

    brand = Brand(name="Balenciaga", slug="balenciaga", aliases=[])
    normalizer = brand_normalizer(policy, brand, ["Balenciaga"])
    title = normalizer.normalize("Yeezy Gap Engineered by Balenciaga Dove Hoodie")
    assert title.tokens == ("dove",)
    assert normalizer.normalize("YZY GAP x Balenciaga Hoodie").tokens == ()
    # Seeds keep their words: no collaboration term eats a model name.
    for seed in policy.seed_models("balenciaga"):
        for alias in seed.aliases:
            assert normalizer.phrase(alias), alias


def test_the_whitelist_is_part_of_the_policy_digest(tmp_path: Path) -> None:
    def drop_puma(directory: Path) -> None:
        path = directory / "models" / "balenciaga.yaml"
        text = path.read_text(encoding="utf-8")
        assert "  - {name: Puma" in text
        lines = [line for line in text.splitlines(keepends=True) if "{name: Puma" not in line]
        path.write_text("".join(lines), encoding="utf-8")

    changed = _policy(tmp_path, drop_puma)
    assert changed.digest != load_policy().digest
    assert "Puma" not in {collab.name for collab in changed.collabs["balenciaga"]}


# --- Regrouping --------------------------------------------------------------------------


def _listing(
    listing_id: int,
    title: str,
    seller: str,
    *,
    path: str = HOODIES,
    designers: tuple[str, ...] = ("Balenciaga",),
    status: str = "active",
) -> Listing:
    created = T0 - timedelta(days=10)
    return Listing(
        source="grailed",
        grailed_id=listing_id,
        status=status,
        url=f"https://www.grailed.com/listings/{listing_id}",
        title=title,
        brand_name_raw="Balenciaga",
        brand_id=1,
        category=path.partition(".")[0],
        subcategory=path,
        category_path=path,
        price=Decimal("100.00"),
        sold_price=Decimal("90.00") if status == "sold" else None,
        sold_at=T0 - timedelta(days=2) if status == "sold" else None,
        currency_original="USD",
        created_at=created,
        first_seen_at=created,
        last_seen_at=T0,
        photo_urls=[],
        designer_names=list(designers),
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
    path: str = HOODIES,
    designers: tuple[str, ...] = ("Balenciaga",),
    status: str = "active",
) -> list[Listing]:
    return [
        _listing(
            start + index,
            title,
            f"{prefix}{index}",
            path=path,
            designers=designers,
            status=status,
        )
        for index in range(sellers)
    ]


async def _database(
    tmp_path: Path, listings: list[Listing]
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'collab.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            Brand(
                id=1,
                name="Balenciaga",
                slug="balenciaga",
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


async def test_a_collaboration_without_a_model_gets_its_line(tmp_path: Path) -> None:
    listings = _rows(100, "Yeezy Gap Engineered by Balenciaga Hoodie", 6, "y")
    listings += [_listing(200, "YGEBB Hoodie", "g")]
    engine, factory = await _database(tmp_path, listings)
    stats = await _regroup(factory)

    line, method = await _placed(factory, 100)
    assert (line.slug, line.product_type, line.name) == (YEEZY, "hoodie", "Yeezy Gap")
    assert (line.status, line.source, line.parent_id, method) == ("auto", "system", None, "collab")
    assert set(line.aliases) == {"yeezy gap", "ygebb", "yzy gap", "engineered by balenciaga"}
    assert group_kind(line.slug, line.product_type) == "collab"
    assert (await _placed(factory, 200))[0].id == line.id
    assert await _group(factory, "engineered") is None  # never mined as a model
    assert (stats.collabs, stats.no_model, stats.with_model) == (7, 0, 0)
    await engine.dispose()


async def test_a_model_wins_and_a_collaboration_wins_over_a_description(tmp_path: Path) -> None:
    listings = [
        _listing(100, "Yeezy Gap Political Campaign Hoodie", "a"),
        _listing(200, "Yeezy Gap Wide Leg Sweatpants", "b", path=SWEATPANTS),
        _listing(300, "Balenciaga Wide Leg Sweatpants", "c", path=SWEATPANTS),
    ]
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory)

    model, how = await _placed(factory, 100)
    assert (model.slug, how) == ("political-campaign", "phrase")
    collab, how = await _placed(factory, 200)
    assert (collab.slug, collab.product_type, how) == (YEEZY, "sweatpants", "collab")
    described, how = await _placed(factory, 300)
    assert (described.slug, how) == ("_desc-wide-leg", "descriptor")
    await engine.dispose()


async def test_designers_alone_do_not_place_a_listing_in_the_line(tmp_path: Path) -> None:
    gap = ("Balenciaga", "Gap", "Yeezy Gap")
    listings = [
        _listing(100, "Balenciaga Hoodie", "a", designers=gap),
        _listing(101, "Balenciaga Wide Leg Sweatpants", "b", designers=gap, path=SWEATPANTS),
        _listing(102, "Balenciaga x Gap Hoodie", "c", designers=gap),  # "Gap" is no alias
        _listing(103, "Yeezy Gap Hoodie", "d", designers=gap),
    ]
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory)

    assert (await _placed(factory, 100))[0].slug == "_none"
    assert (await _placed(factory, 101))[0].slug == "_desc-wide-leg"
    assert (await _placed(factory, 102))[0].slug == "_none"
    assert (await _placed(factory, 103))[0].slug == YEEZY

    async with factory() as session:
        brand = await session.get(Brand, 1)
        assert brand is not None
        report = await collab_report(session, load_policy(), brand)
    rows = {row["collab"]: row for row in report["collab_hints"]}
    assert rows["Yeezy Gap"]["with_designer"] == 4
    assert rows["Yeezy Gap"]["unplaced"] == 3
    assert rows["Yeezy Gap"]["by_kind"] == {"none": 2, "descriptor": 1}
    assert len(rows["Yeezy Gap"]["examples"]) == 3
    assert "Adidas" not in rows  # no listing carries the designer
    await engine.dispose()


async def test_a_model_that_appears_later_takes_the_listing_from_the_line(
    tmp_path: Path,
) -> None:
    listings = _rows(100, "Yeezy Gap Dove Hoodie", 6, "d")
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory)

    dove, how = await _placed(factory, 100)  # six sellers: "Dove" is mined, the model wins
    assert (dove.slug, how) == ("dove", "phrase")
    assert await _group(factory, YEEZY) is None
    await engine.dispose()


async def test_collab_lines_change_nothing_on_a_second_pass_or_a_delta(tmp_path: Path) -> None:
    listings = _rows(100, "Yeezy Gap Engineered by Balenciaga Hoodie", 3, "y")
    listings += _rows(200, "Balenciaga Hoodie", 2, "h")
    engine, factory = await _database(tmp_path, listings)
    await _regroup(factory)
    again = await _regroup(factory, T0 + timedelta(days=1))
    assert (again.changed, again.retired, again.revived, again.groups_created) == (0, 0, 0, 0)
    delta = await _regroup(factory, T0 + timedelta(days=2), full=False)
    assert delta.full is False and delta.processed == 0 and delta.changed == 0
    group = await _group(factory, YEEZY)
    assert group is not None and group.updated_at.replace(tzinfo=UTC) == T0

    async with factory() as session:
        session.add(_listing(300, "YZY GAP Hoodie", "n"))
        await session.commit()
    added = await _regroup(factory, T0 + timedelta(days=3), full=False)
    assert added.full is False and added.processed == 1 and added.groups_created == 0
    assert (await _placed(factory, 300))[0].id == group.id
    await engine.dispose()


async def test_a_collab_line_retires_with_its_titles_and_returns_with_its_id(
    tmp_path: Path,
) -> None:
    engine, factory = await _database(tmp_path, _rows(100, "Yeezy Gap Hoodie", 2, "y"))
    await _regroup(factory)
    line = await _group(factory, YEEZY)
    assert line is not None

    async with factory() as session:
        await session.execute(delete(Listing).where(Listing.grailed_id.in_([100, 101])))
        await session.commit()
    gone = await _regroup(factory, T0 + timedelta(days=1))
    retired = await _group(factory, YEEZY)
    assert gone.retired == 1 and retired is not None and retired.retired_at is not None

    async with factory() as session:
        session.add_all(_rows(200, "Yeezy Gap Hoodie", 1, "n"))
        await session.commit()
    back = await _regroup(factory, T0 + timedelta(days=2))
    revived = await _group(factory, YEEZY)
    assert back.revived == 1 and revived is not None and revived.id == line.id
    assert revived.retired_at is None
    await engine.dispose()


# --- API ---------------------------------------------------------------------------------


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


def _api_database(tmp_path: Path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    listings = _rows(100, "Yeezy Gap Hoodie", 2, "y", status="sold")
    listings += [_listing(200, "Balenciaga Hoodie", "h", status="sold")]
    listings += [_listing(300, "Balenciaga Baggy Hoodie", "b")]
    engine, factory = asyncio.run(_database(tmp_path, listings))
    asyncio.run(_regroup(factory))

    async def metrics() -> None:
        async with factory() as session:
            await MetricsService(session).recompute(as_of=T0)
            await session.commit()

    asyncio.run(metrics())
    return engine, factory


def test_a_collab_line_cannot_be_edited(tmp_path: Path) -> None:
    engine, factory = _api_database(tmp_path)
    line = asyncio.run(_group(factory, YEEZY))
    none = asyncio.run(_group(factory, "_none", "hoodie"))
    assert line is not None and none is not None
    with _api(factory) as client:
        responses = [
            client.post(f"/api/groups/{line.id}/not-model"),
            client.patch(f"/api/groups/{line.id}", json={"name": "Gap"}),
            client.post(f"/api/groups/{line.id}/merge", json={"target_id": none.id}),
        ]
        split = client.post(
            f"/api/groups/{line.id}/split", json={"phrase": "Dove", "as_version": True}
        )
    for response in responses:
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "service_group"
    assert split.status_code == 201, split.text
    assert split.json()["parent_id"] is None  # a service group is never a line
    asyncio.run(engine.dispose())


def test_kind_is_in_group_trend_and_listing_responses(tmp_path: Path) -> None:
    engine, factory = _api_database(tmp_path)
    line = asyncio.run(_group(factory, YEEZY))
    assert line is not None
    with _api(factory) as client:
        groups = client.get("/api/groups", params={"brand_id": 1}).json()["data"]
        detail = client.get(f"/api/groups/{line.id}").json()
        trends = client.get("/api/trends", params={"include_fallback": True}).json()
        card = client.get(f"/api/trends/groups/{line.id}").json()
        catalog = client.get("/api/listings").json()["data"]
        typed = client.get("/api/trends", params={"level": "type"}).json()

    kinds = {row["slug"]: row["kind"] for row in groups}
    assert {slug: kinds[slug] for slug in (YEEZY, "_none", "_desc-baggy", "city")} == {
        YEEZY: "collab",
        "_none": "none",
        "_desc-baggy": "descriptor",
        "city": "model",  # a seed
    }
    assert detail["kind"] == "collab"
    trend_kinds = {row["name"]: row["kind"] for row in trends["data"]}
    assert trend_kinds == {"Yeezy Gap": "collab", "No model": "none"}  # descriptors stay hidden
    assert card["group"]["kind"] == "collab" and card["metrics"]["kind"] == "collab"
    assert {row["kind"] for row in card["recent_sales"]} == {"collab"}
    assert {row["grailed_id"]: row["kind"] for row in catalog} == {
        100: "collab",
        101: "collab",
        200: "none",
        300: "descriptor",
    }
    assert {row["kind"] for row in typed["data"]} == {None}

    share = trends["no_model"]
    assert (share["listings"], share["total_listings"]) == (1, 4)
    assert (share["sold"], share["total_sold"]) == (1, 3)
    assert Decimal(share["listings_share"]) == Decimal("0.25")
    assert Decimal(share["sold_share"]) == Decimal("0.3333")
    asyncio.run(engine.dispose())


def test_the_no_model_share_follows_the_type_scope(tmp_path: Path) -> None:
    engine, factory = _api_database(tmp_path)
    with _api(factory) as client:
        hoodies = client.get("/api/trends", params={"product_type": "hoodie"}).json()
        other = client.get("/api/trends", params={"product_type": "sweatpants"}).json()
    assert hoodies["no_model"]["total_listings"] == 4
    assert other["no_model"] == {
        "listings": 0,
        "total_listings": 0,
        "sold": 0,
        "total_sold": 0,
        "listings_share": None,
        "sold_share": None,
    }
    asyncio.run(engine.dispose())
