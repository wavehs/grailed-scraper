"""The in-app scheduler: one run per daily slot, resumed after throttling, never doubled."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import AppSetting, Base, Brand, Listing, ParserRun
from app.db.session import create_database_engine, get_db
from app.main import app
from app.services import scheduler as scheduler_module
from app.services.parser.launch import LaunchError
from app.services.scheduler import (
    PAUSE_KEY,
    SLOT_ATTEMPTS,
    THROTTLE_PAUSE,
    Scheduler,
    SlotRun,
    decide,
    slot_start,
)

KYIV = timezone(timedelta(hours=3))
# A Thursday; 2026-10-18 is the Sunday after it.
THURSDAY = datetime(2026, 10, 15, 9, tzinfo=KYIV)
SUNDAY = datetime(2026, 10, 18, 9, tzinfo=KYIV)
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"schedule_enabled": True, "schedule_hour": 4, **overrides}
    return Settings(**values)


def _slot_run(status: str, *, throttled: bool = False, attempts: int = 1) -> SlotRun:
    stats: dict[str, Any] = {"schedule": {"slot": "2026-10-15", "attempts": attempts}}
    if throttled:
        stats["stop_reason"] = "throttled"
    return SlotRun(id=1, status=status, stats=stats)


def test_a_slot_starts_at_the_hour_and_lasts_a_day() -> None:
    assert slot_start(THURSDAY, 4) == datetime(2026, 10, 15, 4, tzinfo=KYIV)
    assert slot_start(THURSDAY, 9) == datetime(2026, 10, 15, 9, tzinfo=KYIV)
    assert slot_start(THURSDAY, 10) == datetime(2026, 10, 14, 10, tzinfo=KYIV)


@pytest.mark.parametrize(
    ("settings", "paused", "active", "runs", "action"),
    [
        (_settings(schedule_enabled=False), None, False, [], "idle"),
        (_settings(), THURSDAY + timedelta(hours=1), False, [], "idle"),
        (_settings(), None, True, [], "idle"),
        (_settings(), None, False, [], "start"),
        (_settings(), None, False, [_slot_run("completed")], "done"),
        (_settings(), None, False, [_slot_run("cancelled")], "done"),
        (_settings(), None, False, [_slot_run("partial")], "done"),
        (_settings(), None, False, [_slot_run("partial", throttled=True)], "resume"),
        (_settings(), None, False, [_slot_run("interrupted")], "resume"),
        (_settings(), None, False, [_slot_run("failed")], "start"),
        (
            _settings(),
            None,
            False,
            [_slot_run("interrupted", attempts=SLOT_ATTEMPTS)],
            "give_up",
        ),
        (_settings(), None, False, [_slot_run("failed")] * SLOT_ATTEMPTS, "give_up"),
        # A slot that began 23 hours ago is left for the next one instead of doubling up.
        (_settings(schedule_hour=10), None, False, [], "idle"),
    ],
)
def test_the_decision_for_a_slot(
    settings: Settings,
    paused: datetime | None,
    active: bool,
    runs: list[SlotRun],
    action: str,
) -> None:
    decision = decide(
        now=THURSDAY, settings=settings, paused_until=paused, active=active, slot_runs=runs
    )

    assert decision.action == action, decision


def test_only_the_sunday_slot_is_a_full_run() -> None:
    def full(now: datetime) -> bool:
        return decide(
            now=now, settings=_settings(), paused_until=None, active=False, slot_runs=[]
        ).full

    assert full(SUNDAY) is True
    assert full(THURSDAY) is False


class FakeRuntime:
    def __init__(self) -> None:
        self.active: list[int] = []
        self.started: list[tuple[int, int]] = []

    def start(self, run_id: int, *, settings: Settings | None = None) -> None:
        assert settings is not None
        self.started.append((run_id, settings.requests_per_minute))
        self.active.append(run_id)

    def active_run_ids(self) -> list[int]:
        return list(self.active)


class Harness:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings
        self.now = THURSDAY
        self.runtime = FakeRuntime()
        self.created: list[tuple[list[int] | None, bool]] = []
        self.launch_error: Exception | None = None
        self.maintenance: list[str] = []
        self.scheduler = Scheduler(
            sessions,
            self.runtime,
            settings,
            load_settings=self._load,
            clock=lambda: self.now,
            refresh=self._refresh,
            create=self._create,
            maintenance=self._maintain,
        )

    async def _load(self, session: AsyncSession, base: Settings) -> Settings:
        return self.settings

    async def _refresh(self, session: AsyncSession, settings: Settings) -> None:
        if self.launch_error is not None:
            raise self.launch_error

    async def _create(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        brand_ids: Sequence[int] | None,
        full: bool,
        stats: dict[str, Any] | None = None,
    ) -> ParserRun:
        self.created.append((list(brand_ids) if brand_ids is not None else None, full))
        run = ParserRun(
            source="grailed",
            mode="full" if full else "delta",
            status="pending",
            dry_run=False,
            degraded_mode=False,
            requests_made=0,
            warnings=[],
            stats=dict(stats or {}),
            created_at=self.now,
        )
        session.add(run)
        await session.commit()
        return run

    def _maintain(self, settings: Settings) -> dict[str, object]:
        self.maintenance.append("ran")
        return {"backup": "grailed.sqlite3"}

    async def finish(self, run_id: int, status: str, **stats: Any) -> None:
        async with self.sessions() as session:
            run = await session.get(ParserRun, run_id)
            assert run is not None
            run.status = status
            run.stats = {**run.stats, **stats}
            await session.commit()
        self.runtime.active.remove(run_id)

    async def run(self, run_id: int) -> ParserRun:
        async with self.sessions() as session:
            run = await session.get(ParserRun, run_id)
            assert run is not None
            return run

    async def pause(self) -> object:
        async with self.sessions() as session:
            row = await session.get(AppSetting, PAUSE_KEY)
            return None if row is None else row.value


async def _database(
    path: Path, *, listing_brand: int | None = None
) -> async_sessionmaker[AsyncSession]:
    engine = create_database_engine(Settings(database_url=f"sqlite+aiosqlite:///{path}"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        for brand_id in (1, 2):
            session.add(
                Brand(
                    id=brand_id,
                    name=f"Brand {brand_id}",
                    slug=f"brand-{brand_id}",
                    aliases=[],
                    include_subbrands=False,
                    created_at=T0,
                    updated_at=T0,
                )
            )
        await session.flush()
        if listing_brand is not None:
            session.add(_listing(listing_brand))
        await session.commit()
    return sessions


def _listing(brand_id: int) -> Listing:
    return Listing(
        grailed_id=1,
        status="sold",
        url="https://www.grailed.com/listings/1",
        title="Bomber",
        brand_id=brand_id,
        brand_name_raw="Brand",
        price=100,
        currency_original="USD",
        first_seen_at=T0,
        last_seen_at=T0,
        fetch_tier="T1",
        raw_json={},
        schema_version=1,
    )


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    sessions = await _database(tmp_path / "schedule.db", listing_brand=2)
    yield Harness(sessions, _settings(schedule_rpm=40))


async def test_a_due_slot_starts_one_run_over_the_brands_already_collected(
    harness: Harness,
) -> None:
    await harness.scheduler.tick()
    await harness.scheduler.tick()

    assert harness.created == [([2], False)]
    run_id, rpm = harness.runtime.started[0]
    assert rpm == 40 and len(harness.runtime.started) == 1
    run = await harness.run(run_id)
    assert run.stats["schedule"]["slot"] == "2026-10-15"

    await harness.finish(run_id, "completed")
    await harness.scheduler.tick()
    assert len(harness.created) == 1


async def test_listed_brands_override_the_collected_ones(harness: Harness) -> None:
    harness.settings = _settings(schedule_brand_ids=[1, 2])

    await harness.scheduler.tick()

    assert harness.created == [([1, 2], False)]


async def test_nothing_starts_without_any_collected_brand(tmp_path: Path) -> None:
    sessions = await _database(tmp_path / "empty.db")
    harness = Harness(sessions, _settings())

    await harness.scheduler.tick()

    assert harness.created == []


async def test_a_throttled_run_pauses_the_schedule_then_resumes(harness: Harness) -> None:
    await harness.scheduler.tick()
    run_id = harness.runtime.started[0][0]
    await harness.finish(run_id, "partial", stop_reason="throttled")

    await harness.scheduler.tick()
    paused = await harness.pause()
    assert paused == (THURSDAY + THROTTLE_PAUSE).isoformat()
    assert len(harness.runtime.started) == 1

    harness.now = THURSDAY + THROTTLE_PAUSE + timedelta(minutes=1)
    await harness.scheduler.tick()

    assert harness.runtime.started[-1][0] == run_id and len(harness.runtime.started) == 2
    resumed = await harness.run(run_id)
    assert resumed.status == "pending" and "stop_reason" not in resumed.stats
    assert resumed.stats["schedule"]["attempts"] == 2
    assert resumed.stats["schedule"]["after"] is None
    assert len(harness.created) == 1


async def test_clearing_the_pause_resumes_at_the_next_tick(harness: Harness) -> None:
    await harness.scheduler.tick()
    run_id = harness.runtime.started[0][0]
    await harness.finish(run_id, "partial", stop_reason="throttled")
    await harness.scheduler.tick()

    async with harness.sessions() as session:
        await scheduler_module.clear_pause(session)
        await session.commit()
    await harness.scheduler.tick()

    assert [started for started, _ in harness.runtime.started] == [run_id, run_id]


async def test_a_full_run_is_backed_up_once_after_it_ends(harness: Harness) -> None:
    harness.now = SUNDAY
    await harness.scheduler.tick()
    run_id = harness.runtime.started[0][0]
    assert harness.created == [([2], True)]

    await harness.scheduler.tick()
    assert harness.maintenance == []

    await harness.finish(run_id, "completed")
    await harness.scheduler.tick()
    await harness.scheduler.tick()

    assert harness.maintenance == ["ran"]
    after = (await harness.run(run_id)).stats["schedule"]["after"]
    assert after["maintenance"] == {"backup": "grailed.sqlite3"}


async def test_a_delta_run_is_not_backed_up(harness: Harness) -> None:
    await harness.scheduler.tick()
    await harness.finish(harness.runtime.started[0][0], "completed")

    await harness.scheduler.tick()

    assert harness.maintenance == []


async def test_a_failing_maintenance_is_recorded_once(harness: Harness) -> None:
    def broken(settings: Settings) -> dict[str, object]:
        harness.maintenance.append("broken")
        raise OSError("disk full")

    harness.scheduler = Scheduler(
        harness.sessions,
        harness.runtime,
        harness.settings,
        load_settings=harness._load,
        clock=lambda: harness.now,
        refresh=harness._refresh,
        create=harness._create,
        maintenance=broken,
    )
    harness.now = SUNDAY
    await harness.scheduler.tick()
    await harness.finish(harness.runtime.started[0][0], "partial")

    await harness.scheduler.tick()
    await harness.scheduler.tick()

    assert harness.maintenance == ["broken"]
    after = (await harness.run(harness.runtime.started[0][0])).stats["schedule"]["after"]
    assert after["error"] == "OSError"


async def test_a_slot_gives_up_after_repeated_launch_errors(harness: Harness) -> None:
    harness.launch_error = LaunchError(503, "discovery_failed", "no credentials")

    for _ in range(SLOT_ATTEMPTS + 2):
        await harness.scheduler.tick()
    assert harness.created == []

    harness.launch_error = None
    await harness.scheduler.tick()
    assert harness.created == []

    harness.now = THURSDAY + timedelta(days=1)
    await harness.scheduler.tick()
    assert len(harness.created) == 1


async def test_a_manual_run_holds_the_schedule_back(harness: Harness) -> None:
    harness.runtime.active.append(99)

    await harness.scheduler.tick()

    assert harness.created == []


async def test_the_scheduler_task_stops_cleanly(harness: Harness) -> None:
    harness.scheduler.start()
    await harness.scheduler.close()

    assert harness.created == []


def test_the_schedule_api_reports_and_clears_the_pause(tmp_path: Path) -> None:
    import asyncio

    sessions = asyncio.run(_database(tmp_path / "api.db"))

    async def set_pause() -> None:
        async with sessions() as session:
            session.add(AppSetting(key=PAUSE_KEY, value="2099-01-01T00:00:00+00:00", updated_at=T0))
            await session.commit()

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    asyncio.run(set_pause())
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            before = client.get("/api/parser/schedule")
            cleared = client.delete("/api/parser/schedule/pause")
            after = client.get("/api/parser/schedule")
    finally:
        app.dependency_overrides.clear()

    assert before.status_code == 200, before.text
    body = before.json()
    assert body["paused_until"] == "2099-01-01T00:00:00Z"
    assert body["state"] == "idle" and body["reason"] in {"disabled", "paused"}
    assert set(body) >= {"enabled", "hour", "rpm", "brand_ids", "next_slot_at", "last_run"}
    assert cleared.status_code == 204
    assert after.json()["paused_until"] is None
    assert asyncio.run(_pause_rows(sessions)) == []


async def _pause_rows(sessions: async_sessionmaker[AsyncSession]) -> list[str]:
    async with sessions() as session:
        return list(await session.scalars(select(AppSetting.key)))
