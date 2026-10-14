"""Collection speed and time-left estimate from what the pagination probes already know."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

FINISHED_STATUSES = frozenset({"done", "skipped", "truncated", "failed", "cancelled"})
MIN_ELAPSED_SECONDS = 5


class TaskProgress(Protocol):
    status: str
    hits_collected: int
    expected_hits: int | None


@dataclass(frozen=True, slots=True)
class EtaEstimate:
    hits_expected: int | None
    hits_per_second: float
    eta_seconds: int | None
    is_lower_bound: bool


def estimate_eta(
    tasks: Sequence[TaskProgress], *, hits_fetched: int, elapsed_seconds: int
) -> EtaEstimate:
    """Time left at the average speed so far.

    A task's expected hits are known only once it starts, so while some unfinished task is
    still waiting for its probe the figure is a lower bound.
    """

    known = [task for task in tasks if task.expected_hits is not None]
    hits_expected = sum(task.expected_hits or 0 for task in known) if known else None
    rate = hits_fetched / elapsed_seconds if elapsed_seconds >= MIN_ELAPSED_SECONDS else 0.0
    unfinished = [task for task in tasks if task.status not in FINISHED_STATUSES]
    is_lower_bound = any(task.expected_hits is None for task in unfinished)
    if not unfinished:
        return EtaEstimate(hits_expected, rate, 0, False)
    if rate <= 0 or not any(task.expected_hits is not None for task in unfinished):
        return EtaEstimate(hits_expected, rate, None, is_lower_bound)
    remaining = sum(
        max((task.expected_hits or 0) - task.hits_collected, 0) for task in unfinished
    )
    return EtaEstimate(hits_expected, rate, round(remaining / rate), is_lower_bound)
