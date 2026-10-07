"""grouping-eval: metric definitions on a hand-made fixture with a known answer."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cli import grouping_eval, grouping_eval_sample
from app.core.config import Settings
from app.db.models import Base, Brand, Listing
from app.services.grouping import GroupingService, evaluation
from app.services.grouping.evaluation import (
    GroupView,
    Label,
    LineStat,
    ListingView,
    Share,
    Snapshot,
    Target,
    Verdict,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)

EVAL_YAML = """
brand: {brand}
checkpoints: ["0", "v7a"]
top: [2, 3]
strict_top: 3
tail: {{after: 1, size: 5, seed: 7}}
lost: {{labeled_from: 1, loss_limit: 0}}
collab_watch:
  Yeezy Gap: {{designers: [Gap, Yeezy], title: [yeezy gap, ygebb]}}
targets:
  v7a:
    listings.model_precision: {{min: 0.5}}
    listings.recall_line: {{min: baseline}}
"""


def _group(
    group_id: int,
    name: str,
    *,
    slug: str | None = None,
    product_type: str = "bag",
    parent: int | None = None,
    aliases: tuple[str, ...] = (),
) -> GroupView:
    return GroupView(
        id=group_id,
        product_type=product_type,
        slug=slug or name.casefold().replace(" ", "-"),
        name=name,
        aliases=aliases,
        parent_id=parent,
        status="auto",
        source="mined",
    )


def _view(grailed_id: int, group_id: int | None, *, sold: bool = True) -> ListingView:
    return ListingView(
        grailed_id=grailed_id,
        status="sold" if sold else "active",
        title=f"Balenciaga item {grailed_id}",
        category_path="bags_luggage.totes",
        designers=("Balenciaga",),
        product_type="bag",
        seller=None,
        group_id=group_id,
    )


def _snapshot(assignments: dict[int, int | None]) -> Snapshot:
    groups = [
        _group(1, "City"),
        _group(2, "Mini City", parent=1),
        _group(3, "Campaign"),
        _group(4, "Political Campaign", aliases=("campaign",)),
        _group(5, "No model", slug="_none"),
        _group(6, "Yeezy Gap", slug="_collab-yeezy-gap"),
        _group(7, "Baggy", slug="_desc-baggy"),
        _group(8, "No model", slug="_none", product_type="review"),
    ]
    return Snapshot(
        "balenciaga",
        {group.id: group for group in groups},
        {grailed_id: _view(grailed_id, group) for grailed_id, group in assignments.items()},
    )


def _label(grailed_id: int, gold: str, collab: str = "", borderline: bool = False) -> Label:
    return Label(grailed_id, "", evaluation.parse_gold(gold), collab, borderline)


def test_gold_and_verdict_parsing() -> None:
    assert evaluation.parse_gold("City > Mini City") == evaluation.Gold(
        "model", line="City", leaf="Mini City"
    )
    assert evaluation.parse_gold("NONE").kind == "none"
    assert evaluation.parse_gold("DESCRIPTOR:fit").descriptor == "fit"
    assert evaluation.parse_verdict("duplicate:Political Campaign").detail == "Political Campaign"
    with pytest.raises(ValueError):
        evaluation.parse_verdict("duplicate")
    with pytest.raises(ValueError):
        evaluation.parse_gold("City >")
    assert evaluation.name_keys("Speed Hunters") & evaluation.name_keys("speedhunters")
    assert evaluation.name_keys("X-Pander") & evaluation.name_keys("Xpander")
    assert not evaluation.name_keys("Pander") & evaluation.name_keys("X-Pander")


def test_wilson_interval_matches_the_textbook_value() -> None:
    interval = evaluation.wilson(9, 10)
    assert interval is not None
    assert round(interval[0], 3) == 0.596 and round(interval[1], 3) == 0.982
    assert evaluation.wilson(0, 0) is None


def test_listing_metrics_follow_the_definitions() -> None:
    current = _snapshot(
        {
            1: 2,  # gold City, system Mini City: right line, wrong version
            2: 1,  # gold Mini City, system City: right line, wrong version
            3: 3,  # gold Political Campaign, system Campaign: duplicate line, wrong
            4: 4,  # gold Political Campaign via alias: right
            5: 5,  # gold NONE in none: right
            6: 7,  # gold DESCRIPTOR in descriptor: right
            7: 6,  # collab only, right partner
            8: 3,  # collab only in a model group: not in model precision, collab miss
            9: 5,  # gold Strike in none: recall miss, none-precision miss
            10: 1,  # borderline: excluded
            11: 8,  # review
        }
    )
    labels = [
        _label(1, "City"),
        _label(2, "City > Mini City"),
        _label(3, "Political Campaign"),
        _label(4, "Political Campaign"),
        _label(5, "NONE"),
        _label(6, "DESCRIPTOR:fit"),
        _label(7, "NONE", collab="Yeezy Gap"),
        _label(8, "DESCRIPTOR:fit", collab="Yeezy Gap"),
        _label(9, "Strike"),
        _label(10, "City", borderline=True),
        _label(11, "NONE"),
        _label(404, "NONE"),
    ]
    baseline = _snapshot({1: 1, 2: 1, 3: 3, 4: 3, 9: 5, 5: 5, 6: 5, 7: 5, 8: 3, 10: 1, 11: 8})
    result = evaluation.listing_metrics(labels, current, baseline)
    shares = result["shares"]
    assert (result["missing"], result["borderline"], result["review"]) == (1, 1, 1)
    assert shares["model_precision"] == Share(3, 4)
    assert shares["model_precision_strict"] == Share(1, 4)
    assert shares["recall_line"] == Share(3, 5)
    assert shares["none_precision"] == Share(2, 3)
    assert shares["descriptor_precision"] == Share(1, 1)
    assert shares["collab_precision"] == Share(1, 1)
    assert shares["collab_recall"] == Share(1, 2)
    # The baseline had 1 and 2 right; both stay right.
    assert shares["recall_regression"] == Share(2, 2)


def test_phrase_metrics_tail_and_lost_groups() -> None:
    snapshot = _snapshot({1: 1, 2: 1, 3: 3, 4: 4, 5: 4, 6: 4, 7: 2})
    lines = evaluation.rank_lines(snapshot)
    assert [(item.line.name, item.sales) for item in lines] == [
        ("City", 3),  # ties go by type and slug
        ("Political Campaign", 3),
        ("Campaign", 1),
    ]
    verdicts = {
        ("bag", "political-campaign"): Verdict("model"),
        ("bag", "city"): Verdict("not_model", "fit"),
    }
    top = evaluation.phrase_metrics(lines, verdicts, 3)
    assert top["garbage_lines"] == round(1 / 3, 4)
    assert top["garbage_sales"] == round(3 / 7, 4)
    assert [item["slug"] for item in top["unlabeled"]] == ["campaign"]
    draws = evaluation.tail_sample(lines, after=1, size=20, seed=1)
    assert {item.line.name for item in draws} <= {"Political Campaign", "Campaign"}
    tail = evaluation.tail_metrics(draws, verdicts)
    assert tail["garbage_sales"].total + sum(
        1 for item in draws if item.key not in verdicts
    ) == 20

    current = _snapshot({1: 1, 2: 1, 3: 5, 4: 4, 5: 4, 6: 4, 7: 2})
    del current.groups[3]
    verdicts[("bag", "campaign")] = Verdict("model")
    lost = evaluation.lost_groups(snapshot, current, verdicts)
    assert lost["loss_sales"] == 1 and lost["rows"][0]["name"] == "Campaign"


def test_targets_use_points_and_wilson_bounds() -> None:
    values = {"a": Share(950, 1000), "b": Share(9, 10), "c": 0.02, "d": Share(5, 10)}
    results = evaluation.check_targets(
        [
            Target("a", "min", 0.9),
            Target("b", "min", 0.9),  # the point passes, the Wilson bound (0.60) does not
            Target("c", "max", 0.03),
            Target("d", "min", "baseline"),
            Target("missing", "min", 0.5),
        ],
        values,
        {"d": Share(4, 10)},
    )
    assert [item["passed"] for item in results] == [True, False, True, True, False]


def test_split_is_stable_and_about_forty_percent() -> None:
    holdout = sum(evaluation.split_of(item) == "holdout" for item in range(10_000))
    assert 3_800 < holdout < 4_200
    assert evaluation.split_of(12345) == evaluation.split_of(12345)


def _write_eval_dir(directory: Path, labels: list[dict[str, str]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "eval.yaml").write_text(EVAL_YAML.format(brand="balenciaga"), encoding="utf-8")
    evaluation.write_sheet(directory / "labels.csv", evaluation.LABEL_COLUMNS, labels)


def _listing(listing_id: int, title: str, path: str, seller: str, sold: bool) -> Listing:
    created = NOW - timedelta(days=listing_id % 30 + 1)
    return Listing(
        source="grailed",
        grailed_id=listing_id,
        status="sold" if sold else "active",
        url=f"https://www.grailed.com/listings/{listing_id}",
        title=title,
        brand_name_raw="Balenciaga",
        brand_id=1,
        category=path.partition(".")[0],
        subcategory=path,
        category_path=path,
        price=Decimal("100.00"),
        currency_original="USD",
        sold_price=Decimal("100.00") if sold else None,
        created_at=created,
        sold_at=NOW if sold else None,
        first_seen_at=created,
        last_seen_at=NOW,
        photo_urls=[],
        designer_names=["Balenciaga"],
        seller_identity=seller,
        seller_identity_mode="hashed",
        quality_flags=[],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
    )


async def _database(path: Path) -> Settings:
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Brand(
                id=1,
                name="Balenciaga",
                slug="balenciaga",
                aliases=[],
                include_subbrands=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        rows = [
            _listing(index, f"Balenciaga Triple S Sneakers {index}", "footwear.lowtop_sneakers",
                     f"s{index}", index % 2 == 0)
            for index in range(1, 9)
        ]
        rows += [
            _listing(20 + index, "Balenciaga Baggy Jeans", "bottoms.denim", f"b{index}", True)
            for index in range(6)
        ]
        rows.append(_listing(40, "Balenciaga Hoodie", "tops.sweatshirts_hoodies", "h", True))
        session.add_all(rows)
        await session.commit()
        await GroupingService(session).regroup()
        await session.commit()
    await engine.dispose()
    return Settings(database_url=url)


async def test_grouping_eval_end_to_end_on_a_fixture_database(tmp_path: Path) -> None:
    settings = await _database(tmp_path / "eval.db")
    directory = tmp_path / "eval" / "balenciaga"
    labels = [
        {"grailed_id": str(index), "gold_model": "Triple S"} for index in range(1, 9)
    ] + [{"grailed_id": "20", "gold_model": "DESCRIPTOR:fit"}, {"grailed_id": "40",
                                                                "gold_model": "NONE"}]
    _write_eval_dir(directory, labels)

    report = await grouping_eval(
        settings, brand="balenciaga", split="all", checkpoint=None, strict=False,
        baseline_db=None, directory=directory,
    )
    shares = report["listings"]["shares"]  # type: ignore[index]
    assert shares["recall_line"]["point"] == 1.0
    assert report["status"] == "ok"
    # "Baggy" is mined as a model today: unlabeled in the top lines is only a warning…
    assert any("unlabeled" in item for item in report["warnings"])  # type: ignore[attr-defined]
    strict = await grouping_eval(
        settings, brand="balenciaga", split="dev", checkpoint=None, strict=True,
        baseline_db=None, directory=directory,
    )
    assert strict["status"] == "failed"  # …and a failure with --strict

    with pytest.raises(RuntimeError, match="checkpoint"):
        await grouping_eval(
            settings, brand="balenciaga", split="holdout", checkpoint=None, strict=False,
            baseline_db=None, directory=directory,
        )
    with pytest.raises(RuntimeError, match="Unknown checkpoint"):
        await grouping_eval(
            settings, brand="balenciaga", split="holdout", checkpoint="later", strict=False,
            baseline_db=None, directory=directory,
        )
    held = await grouping_eval(
        settings, brand="balenciaga", split="holdout", checkpoint="v7a", strict=False,
        baseline_db=tmp_path / "eval.db", directory=directory,
    )
    assert held["holdout_runs"] == 1
    with (directory / "holdout_runs.csv").open(encoding="utf-8", newline="") as handle:
        (entry,) = list(csv.DictReader(handle))
    assert entry["checkpoint"] == "v7a" and len(entry["labels_sha256"]) == 64

    evaluation.seal_manifest(directory, labeler="test", external_check="none")
    sealed = await grouping_eval(
        settings, brand="balenciaga", split="all", checkpoint=None, strict=False,
        baseline_db=None, directory=directory,
    )
    assert sealed["manifest"]["matches"] is True  # type: ignore[index]
    assert sealed["limitations"]


async def test_sample_sheets_are_blind_and_import_never_replaces(tmp_path: Path) -> None:
    settings = await _database(tmp_path / "sample.db")
    directory = tmp_path / "eval" / "balenciaga"
    _write_eval_dir(directory, [{"grailed_id": "1", "gold_model": "Triple S"}])
    out = tmp_path / "sheets"
    result = await grouping_eval_sample(
        settings, brand="balenciaga", out=out, sheets=("listings", "phrases"), sold=3,
        active=2, top=10, directory=directory,
    )
    assert result["written"] == {"listings.csv": 5, "phrases.csv": 2}
    with (out / "listings.csv").open(encoding="utf-8", newline="") as handle:
        sheet = list(csv.DictReader(handle))
    assert "1" not in {row["grailed_id"] for row in sheet}  # already labeled
    assert set(sheet[0]) == set(evaluation.SHEET_LISTING_COLUMNS)  # no group, rank or counts
    sheet[0]["gold_model"] = "NONE"
    sheet[1]["gold_model"] = "Triple S"
    evaluation.write_sheet(out / "listings.csv", evaluation.SHEET_LISTING_COLUMNS, sheet)
    assert evaluation.import_sheet(directory, out / "listings.csv")["added"] == 2
    assert evaluation.import_sheet(directory, out / "listings.csv") == {
        "target": "labels.csv",
        "added": 0,
        "skipped": 2,
    }
    with (out / "phrases.csv").open(encoding="utf-8", newline="") as handle:
        phrases = list(csv.DictReader(handle))
    assert "examples" in phrases[0] and "sales" not in phrases[0]
    for row in phrases:
        row["verdict"] = "model"
    evaluation.write_sheet(out / "phrases.csv", evaluation.SHEET_PHRASE_COLUMNS, phrases)
    assert evaluation.import_sheet(directory, out / "phrases.csv")["added"] == 2
    lines = evaluation.rank_lines(
        await _load(settings)
    )
    assert all(isinstance(item, LineStat) for item in lines)


async def _load(settings: Settings) -> Snapshot:
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            return await evaluation.load_snapshot(session, "balenciaga")
    finally:
        await engine.dispose()


def test_canon_accepts_spellings_but_not_fragments(tmp_path: Path) -> None:
    path = tmp_path / "canon.csv"
    path.write_text(
        "canon,aliases\nLe Cagole,Cagole\nPolitical Campaign,Politixal Campaign\n",
        encoding="utf-8",
    )
    canon = evaluation.load_canon(path)
    cagole = _group(9, "Cagole")
    snapshot = Snapshot(
        "balenciaga",
        {3: _group(3, "Campaign"), 9: cagole},
        {1: _view(1, 9), 2: _view(2, 3)},
    )
    spelled = evaluation.line_correct(
        _label(1, "Le Cagole"), snapshot.placement(1), canon=canon
    )
    fragment = evaluation.line_correct(
        _label(2, "Political Campaign"), snapshot.placement(2), canon=canon
    )
    assert spelled and not fragment


def test_label_hash_ignores_line_endings(tmp_path: Path) -> None:
    unix, windows = tmp_path / "unix.csv", tmp_path / "windows.csv"
    unix.write_bytes(b"a,b\n1,2\n")
    windows.write_bytes(b"a,b\r\n1,2\r\n")
    assert evaluation.file_sha256(unix) == evaluation.file_sha256(windows)
