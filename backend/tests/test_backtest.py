"""The metrics backtest scores the state at T against what happened after T (plan 2.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli import metrics_backtest
from app.core.config import Settings
from app.db.models import Base, Brand, Listing, ListingModelAssignment, ModelGroup
from app.services.metrics.backtest import run_backtest, spearman, write_report
from app.services.metrics.service import MetricsService

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
T = NOW - timedelta(days=30)


def test_spearman_ranks_ties_by_their_average() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    # Ranks (1, 2.5, 2.5, 4) against (1, 2, 3, 4).
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833)


def test_spearman_is_undefined_without_spread_or_pairs() -> None:
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1], [2]) is None


def _listing(
    listing_id: int,
    group_id: int,
    *,
    created_days: int,
    sold_days: int | None = None,
    removed_days: int | None = None,
) -> tuple[Listing, int]:
    created = NOW - timedelta(days=created_days)
    sold_at = NOW - timedelta(days=sold_days) if sold_days is not None else None
    status = "sold" if sold_at else "removed" if removed_days is not None else "active"
    listing = Listing(
        id=listing_id,
        grailed_id=listing_id,
        status=status,
        url=f"https://www.grailed.com/listings/{listing_id}",
        title="Item",
        brand_name_raw="Rick Owens",
        brand_id=1,
        product_type="boots",
        price=Decimal(500),
        sold_price=Decimal(450) if sold_at else None,
        currency_original="USD",
        condition="is_used",
        created_at=created,
        sold_at=sold_at,
        first_seen_at=created,
        last_seen_at=NOW,
        removed_checked_at=NOW - timedelta(days=removed_days) if removed_days else None,
        photo_urls=[],
        seller_identity=f"seller-{listing_id % 7}",
        seller_identity_mode="hashed",
        quality_flags=[],
        fetch_tier="T1",
        raw_json={},
        schema_version=2,
        department="menswear",
    )
    return listing, group_id


def _rows() -> list[tuple[Listing, int]]:
    """Geobasket sold steadily before T and keeps selling; Kiss sold little and stalls."""

    rows: list[tuple[Listing, int]] = []
    next_id = iter(range(1, 1000))
    for days in range(35, 120, 5):
        rows.append(_listing(next(next_id), 10, created_days=days + 10, sold_days=days))
    for days in range(2, 28, 3):
        rows.append(_listing(next(next_id), 10, created_days=60, sold_days=days))
    rows += [_listing(next(next_id), 10, created_days=40) for _ in range(4)]
    rows += [_listing(next(next_id), 11, created_days=200) for _ in range(8)]
    rows.append(_listing(next(next_id), 11, created_days=150, sold_days=100))
    rows.append(_listing(next(next_id), 11, created_days=150, sold_days=10))
    # Removed after T: still supply at T.
    rows.append(_listing(next(next_id), 11, created_days=150, removed_days=5))
    rows += [_listing(next(next_id), 12, created_days=90, sold_days=days) for days in (40, 50, 60)]
    rows += [_listing(next(next_id), 12, created_days=90) for _ in range(3)]
    # Created after T: unknown at T.
    rows += [_listing(next(next_id), 12, created_days=10) for _ in range(5)]
    return rows


async def _database(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Brand(
                id=1,
                name="Rick Owens",
                slug="rick-owens",
                aliases=[],
                include_subbrands=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add_all(
            ModelGroup(
                id=group_id,
                brand_id=1,
                product_type="boots",
                slug=slug,
                name=slug.title(),
                aliases=[],
                status="auto",
                source="mined",
                created_at=NOW,
                updated_at=NOW,
            )
            for group_id, slug in ((10, "geobasket"), (11, "kiss"), (12, "creeper"))
        )
        rows = _rows()
        session.add_all(listing for listing, _ in rows)
        await session.flush()
        session.add_all(
            ListingModelAssignment(
                listing_id=listing.id,
                model_group_id=group_id,
                method="test",
                input_hash="x",
                updated_at=NOW,
            )
            for listing, group_id in rows
        )
        await session.commit()
    return factory


async def test_the_state_at_t_reopens_what_happened_later(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "state.db")

    async with factory() as session:
        scopes = await MetricsService(session).scopes(1, T, historical=True)
    kiss = scopes.by_scope["model:11"]
    creeper = scopes.by_scope["model:12"]

    # Kiss: eight active, the later sale and the later removal are active at T too.
    assert sum(item.status == "active" for item in kiss) == 10
    assert all(item.sold_at is None for item in kiss if item.status == "active")
    # The five creepers listed after T are not there yet.
    assert len(creeper) == 6


async def test_the_backtest_scores_groups_against_their_future(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "backtest.db")

    async with factory() as session:
        report = await run_backtest(session, as_of_days=30, now=NOW)

    outcomes = {outcome.key: outcome for outcome in report.outcomes}
    assert set(outcomes) == {"model:10", "model:11", "model:12"}
    geobasket, kiss = outcomes["model:10"], outcomes["model:11"]
    assert geobasket.sold_after == 9 and kiss.sold_after == 1
    assert kiss.supply_at == 10
    assert kiss.forward_sell_through == pytest.approx(0.1)
    assert geobasket.liquidity_score is not None
    assert geobasket.liquidity_score > (kiss.liquidity_score or Decimal(0))
    volume = {row.metric: row for row in report.volume}
    assert volume["sold_30d"].groups == 3
    assert volume["liquidity_score"].rho is not None
    velocity = {row.metric for row in report.velocity}
    assert velocity == {"p30", "liquidity_score", "p30_raw", "sell_through_30d"}
    assert [(item.target, item.metric) for item in report.verdicts] == [
        ("volume", "depth"),
        ("volume", "liquidity_score"),
        ("velocity", "p30"),
        ("velocity", "liquidity_score"),
    ]


async def test_the_report_is_written_as_markdown(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "report.db")
    async with factory() as session:
        report = await run_backtest(session, as_of_days=30, now=NOW)

    path = write_report(report, tmp_path / "eval")

    assert path.name == "backtest-2026-10-15.md"
    text = path.read_text(encoding="utf-8")
    assert "Spearman" in text and "liquidity_score" in text and "sell_through_30d" in text


async def test_the_cli_writes_the_report_under_the_given_folder(tmp_path: Path) -> None:
    await _database(tmp_path / "cli.db")
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")

    result = await metrics_backtest(settings, as_of_days=30, directory=tmp_path / "out")

    assert result["status"] == "ok" and result["groups"] == 3
    assert Path(str(result["report"])).parent == tmp_path / "out"


async def test_a_backtest_needs_at_least_one_day(tmp_path: Path) -> None:
    factory = await _database(tmp_path / "days.db")
    async with factory() as session:
        with pytest.raises(ValueError, match="as_of_days"):
            await run_backtest(session, as_of_days=0, now=NOW)
