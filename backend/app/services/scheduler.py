"""Daily collection inside the app: one run per slot, resumed after throttling, never doubled.

A slot opens every day at ``schedule_hour`` local time and stays open until the next one.
The scheduler keeps no state of its own that matters: every run it starts carries
``stats["schedule"]`` (slot, attempts, the after-run outcome), so a restart picks up where
the previous process left off. The Sunday slot is a full run, followed by a backup and
retention; a run stopped for throttling pauses the schedule for six hours, then resumes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import AppSetting, Listing, ParserRun
from app.repositories.runs import RunRepository
from app.services.operations import backup_database, result_dict, retention
from app.services.parser.launch import (
    RUN_RPM_KEY,
    create_run,
    refresh_stale_discovery,
    run_settings,
)
from app.services.parser.runtime import THROTTLED

CHECK_INTERVAL_S = 600.0
# Long enough that a short-lived app (tests, a quick restart) never launches anything.
STARTUP_DELAY_S = 60.0
THROTTLE_PAUSE = timedelta(hours=6)
# Starts, resumes and launch errors together, per slot.
SLOT_ATTEMPTS = 3
# The wait after a failed launch; it doubles with each failure of the same slot.
LAUNCH_BACKOFF = timedelta(minutes=30)
# A failed backup or retention is retried this long after the last try, a few times.
MAINTENANCE_RETRY = timedelta(hours=1)
MAINTENANCE_ATTEMPTS = 3
FULL_WEEKDAY = 6  # Sunday
# A slot found later than this (the Mac slept through it) is left for the next one.
CATCH_UP = timedelta(hours=20)
PAUSE_KEY = "schedule_paused_until"
SCHEDULE_KEY = "schedule"
RECENT_RUNS = 20
_ACTIVE = ("pending", "running")
_DONE = frozenset({"completed", "partial", "cancelled"})
_MAINTAINED = frozenset({"completed", "partial"})

Action = Literal["idle", "start", "resume", "done", "give_up"]
SettingsLoader = Callable[[AsyncSession, Settings], Awaitable[Settings]]
Refresh = Callable[[AsyncSession, Settings], Awaitable[None]]
Create = Callable[..., Awaitable[ParserRun]]
Maintenance = Callable[[Settings], dict[str, object]]


class RunStarter(Protocol):
    launch_lock: asyncio.Lock

    def start(self, run_id: int, *, settings: Settings | None = None) -> None: ...

    def active_run_ids(self) -> list[int]: ...


@dataclass(frozen=True, slots=True)
class SlotLaunches:
    """Failed launches of one slot; kept in memory, so a restart forgets them."""

    failures: int = 0
    retry_at: datetime | None = None

    def failed(self, now: datetime) -> SlotLaunches:
        failures = self.failures + 1
        return SlotLaunches(failures, now + LAUNCH_BACKOFF * 2 ** (failures - 1))


@dataclass(frozen=True, slots=True)
class SlotRun:
    id: int
    status: str
    stats: dict[str, Any]

    @property
    def resumable(self) -> bool:
        return self.status == "interrupted" or (
            self.status == "partial" and self.stats.get("stop_reason") == THROTTLED
        )

    @property
    def attempts(self) -> int:
        value = _schedule(self.stats).get("attempts", 1)
        return value if isinstance(value, int) else 1


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    reason: str
    slot: datetime
    full: bool = False
    run_id: int | None = None

    @property
    def key(self) -> str:
        return slot_key(self.slot)


@dataclass(frozen=True, slots=True)
class ScheduleState:
    decision: Decision
    paused_until: datetime | None
    last_run: ParserRun | None
    next_slot_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "next_slot_at", self.decision.slot + timedelta(days=1))


def slot_start(now: datetime, hour: int) -> datetime:
    """The start of the slot ``now`` falls in: today at ``hour``, or yesterday before it."""

    today = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return today if now >= today else today - timedelta(days=1)


def slot_key(slot: datetime) -> str:
    return slot.date().isoformat()


def decide(
    *,
    now: datetime,
    settings: Settings,
    paused_until: datetime | None,
    active: bool,
    slot_runs: Sequence[SlotRun],
    launches: SlotLaunches | None = None,
    has_brands: bool = True,
) -> Decision:
    """What the current slot needs; ``slot_runs`` are its scheduled runs, oldest first."""

    slot = slot_start(now, settings.schedule_hour)
    if not settings.schedule_enabled:
        return Decision("idle", "disabled", slot)
    if paused_until is not None and now < paused_until:
        return Decision("idle", "paused", slot)
    if active:
        return Decision("idle", "run_active", slot)
    latest = slot_runs[-1] if slot_runs else None
    if latest is not None and not latest.resumable and latest.status in _DONE:
        return Decision("done", latest.status, slot)
    launched = launches or SlotLaunches()
    if sum(run.attempts for run in slot_runs) + launched.failures >= SLOT_ATTEMPTS:
        return Decision("give_up", "attempts", slot)
    if launched.retry_at is not None and now < launched.retry_at:
        return Decision("idle", "backoff", slot)
    if latest is not None and latest.resumable:
        return Decision("resume", latest.status, slot, run_id=latest.id)
    if not slot_runs and now - slot > CATCH_UP:
        return Decision("idle", "missed", slot)
    if not has_brands:
        # Brands nobody collected yet are never picked up on their own (D4).
        return Decision("idle", "no_brands", slot)
    return Decision("start", "due", slot, full=slot.weekday() == FULL_WEEKDAY)


async def read_state(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime,
    active_run_ids: Sequence[int],
    launches: Mapping[str, SlotLaunches] | None = None,
) -> ScheduleState:
    paused_until = await _paused_until(session)
    active = bool(active_run_ids) or await _any_active(session)
    scheduled = await _recent_scheduled(session)
    key = slot_key(slot_start(now, settings.schedule_hour))
    slot_runs = [
        SlotRun(run.id, run.status, run.stats)
        for run in reversed(scheduled)
        if _schedule(run.stats).get("slot") == key
    ]
    decision = decide(
        now=now,
        settings=settings,
        paused_until=paused_until,
        active=active,
        slot_runs=slot_runs,
        launches=(launches or {}).get(key),
        has_brands=bool(settings.schedule_brand_ids) or await _has_collected_brand(session),
    )
    return ScheduleState(decision, paused_until, scheduled[0] if scheduled else None)


async def clear_pause(session: AsyncSession) -> None:
    await session.execute(delete(AppSetting).where(AppSetting.key == PAUSE_KEY))


def weekly_maintenance(settings: Settings) -> dict[str, object]:
    """Back up first, so retention never purges raw data the newest backup lacks."""

    backup = backup_database(settings)
    return {"backup": str(backup), "retention": result_dict(retention(settings, apply=True))}


class Scheduler:
    """Check the slot every few minutes and start, resume or follow up scheduled runs."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runtime: RunStarter,
        base: Settings,
        *,
        load_settings: SettingsLoader,
        market_lock: asyncio.Lock | None = None,
        clock: Callable[[], datetime] | None = None,
        refresh: Refresh = refresh_stale_discovery,
        create: Create = create_run,
        maintenance: Maintenance = weekly_maintenance,
    ) -> None:
        self._sessions = sessions
        self._runtime = runtime
        self._base = base
        self._load_settings = load_settings
        self._market_lock = market_lock or asyncio.Lock()
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._refresh = refresh
        self._create = create
        self._maintenance = maintenance
        self._launches: dict[str, SlotLaunches] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._logged: tuple[str, str, str] | None = None

    @property
    def launches(self) -> Mapping[str, SlotLaunches]:
        return dict(self._launches)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run_forever(), name="parser-scheduler")

    def wake(self) -> None:
        self._wake.set()

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def run_forever(self) -> None:
        await self._sleep(STARTUP_DELAY_S)
        while True:
            try:
                await self.tick()
            except Exception:
                structlog.get_logger(__name__).exception("schedule_tick_failed")
            await self._sleep(CHECK_INTERVAL_S)

    async def tick(self) -> Decision:
        now = self._clock()
        async with self._sessions() as session:
            settings = await self._load_settings(session, self._base)
        if settings.schedule_enabled:
            await self._follow_up(settings, now)
        async with self._sessions() as session:
            state = await read_state(
                session,
                settings,
                now=now,
                active_run_ids=self._runtime.active_run_ids(),
                launches=self._launches,
            )
        decision = state.decision
        self._log(decision)
        if decision.action in ("start", "resume"):
            try:
                await self._launch(settings, decision)
            except Exception as exc:
                previous = self._launches.get(decision.key, SlotLaunches())
                self._launches[decision.key] = previous.failed(now)
                structlog.get_logger(__name__).warning(
                    "schedule_launch_failed", slot=decision.key, error_type=type(exc).__name__
                )
        return decision

    async def _launch(self, settings: Settings, decision: Decision) -> None:
        # The same lock as a manual start: the refresh awaits the network, and a run
        # created by hand meanwhile must not get a scheduled twin.
        async with self._runtime.launch_lock, self._sessions() as session:
            await self._refresh(session, settings)
            if self._runtime.active_run_ids() or await _any_active(session):
                structlog.get_logger(__name__).info("schedule_run_active", slot=decision.key)
                return
            if decision.action == "start":
                run = await self._create_scheduled(session, settings, decision)
                if run is None:
                    return
            else:
                assert decision.run_id is not None
                run = await RunRepository(session).prepare_resume(decision.run_id)
                attempts = SlotRun(run.id, run.status, run.stats).attempts + 1
                run.stats = {
                    **run.stats,
                    SCHEDULE_KEY: {**_schedule(run.stats), "attempts": attempts},
                }
                await session.commit()
            self._runtime.start(run.id, settings=run_settings(settings, run))
        structlog.get_logger(__name__).info(
            "schedule_run_launched", run_id=run.id, action=decision.action, slot=decision.key
        )

    async def _create_scheduled(
        self, session: AsyncSession, settings: Settings, decision: Decision
    ) -> ParserRun | None:
        brand_ids = settings.schedule_brand_ids or await _collected_brands(session)
        if not brand_ids:
            structlog.get_logger(__name__).info("schedule_no_brands", slot=decision.key)
            return None
        schedule = {"slot": decision.key, "full": decision.full, "attempts": 1, "after": None}
        return await self._create(
            session,
            settings,
            brand_ids=brand_ids,
            full=decision.full,
            stats={RUN_RPM_KEY: settings.schedule_rpm, SCHEDULE_KEY: schedule},
        )

    async def _follow_up(self, settings: Settings, now: datetime) -> None:
        """Pause after throttling and maintain after a full run, once per finished run."""

        active = set(self._runtime.active_run_ids())
        async with self._sessions() as session:
            finished = [
                SlotRun(run.id, run.status, run.stats)
                for run in await _recent_scheduled(session)
                if _needs_follow_up(_schedule(run.stats).get("after"), now)
                and run.status not in _ACTIVE
                and run.id not in active
            ]
        for run in reversed(finished):
            after = await self._after(settings, run, now)
            if after is None:
                continue
            async with self._sessions() as session:
                stored = await session.get(ParserRun, run.id)
                if stored is None:
                    continue
                stored.stats = {
                    **stored.stats,
                    SCHEDULE_KEY: {**_schedule(stored.stats), "after": after},
                }
                await session.commit()

    async def _after(
        self, settings: Settings, run: SlotRun, now: datetime
    ) -> dict[str, Any] | None:
        after: dict[str, Any] = {"at": now.isoformat()}
        log = structlog.get_logger(__name__)
        if run.stats.get("stop_reason") == THROTTLED:
            until = now + THROTTLE_PAUSE
            async with self._sessions() as session:
                await session.merge(
                    AppSetting(key=PAUSE_KEY, value=until.isoformat(), updated_at=now)
                )
                await session.commit()
            log.warning("schedule_paused", run_id=run.id, until=until.isoformat())
            return {**after, "paused_until": until.isoformat()}
        if not (_schedule(run.stats).get("full") and run.status in _MAINTAINED):
            return after
        previous = _schedule(run.stats).get("after")
        failures = _failures(previous) if isinstance(previous, dict) else 0
        async with self._market_lock:
            if self._runtime.active_run_ids():
                return None  # A manual run slipped in; maintain once it is over.
            try:
                result = await asyncio.to_thread(self._maintenance, settings)
            except Exception as exc:
                log.exception("schedule_maintenance_failed", run_id=run.id)
                return {**after, "error": type(exc).__name__, "failures": failures + 1}
        log.info("schedule_maintenance_done", run_id=run.id)
        return {**after, "maintenance": result}

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except TimeoutError:
            pass
        self._wake.clear()

    def _log(self, decision: Decision) -> None:
        current = (decision.key, decision.action, decision.reason)
        if current != self._logged:
            self._logged = current
            structlog.get_logger(__name__).info(
                "schedule_state", slot=decision.key, action=decision.action, reason=decision.reason
            )


def _needs_follow_up(after: object, now: datetime) -> bool:
    """Not followed up yet, or a failed maintenance that is due another try."""

    if after is None:
        return True
    if not isinstance(after, dict) or not after.get("error"):
        return False
    if _failures(after) >= MAINTENANCE_ATTEMPTS:
        return False
    try:
        tried = datetime.fromisoformat(str(after.get("at")))
    except ValueError:
        return True
    return now - tried >= MAINTENANCE_RETRY


def _failures(after: Mapping[str, Any]) -> int:
    value = after.get("failures", 1 if after.get("error") else 0)
    return value if isinstance(value, int) else 1


def _schedule(stats: Mapping[str, Any]) -> dict[str, Any]:
    value = stats.get(SCHEDULE_KEY)
    return dict(value) if isinstance(value, dict) else {}


async def _paused_until(session: AsyncSession) -> datetime | None:
    row = await session.get(AppSetting, PAUSE_KEY)
    if row is None or not isinstance(row.value, str):
        return None
    try:
        until = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    return until if until.tzinfo is not None else None


async def _recent_scheduled(session: AsyncSession) -> list[ParserRun]:
    """Scheduled runs among the most recent ones, newest first."""

    runs = await session.scalars(select(ParserRun).order_by(ParserRun.id.desc()).limit(RECENT_RUNS))
    return [run for run in runs if _schedule(run.stats).get("slot")]


async def _any_active(session: AsyncSession) -> bool:
    found = await session.scalar(select(ParserRun.id).where(ParserRun.status.in_(_ACTIVE)).limit(1))
    return found is not None


async def _has_collected_brand(session: AsyncSession) -> bool:
    found = await session.scalar(select(Listing.id).where(Listing.brand_id.is_not(None)).limit(1))
    return found is not None


async def _collected_brands(session: AsyncSession) -> list[int]:
    rows = await session.scalars(
        select(Listing.brand_id).where(Listing.brand_id.is_not(None)).distinct()
    )
    return sorted(brand_id for brand_id in rows if brand_id is not None)
