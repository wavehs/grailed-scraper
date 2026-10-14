from __future__ import annotations

from dataclasses import dataclass

from app.services.parser.eta import estimate_eta


@dataclass
class Task:
    status: str
    hits_collected: int
    expected_hits: int | None


def test_eta_uses_average_speed_over_remaining_expected_hits() -> None:
    tasks = [Task("done", 1_000, 1_000), Task("running", 1_000, 5_000)]

    estimate = estimate_eta(tasks, hits_fetched=2_000, elapsed_seconds=100)

    assert estimate.hits_expected == 6_000
    assert estimate.hits_per_second == 20.0
    assert estimate.eta_seconds == 200
    assert estimate.is_lower_bound is False


def test_eta_is_a_lower_bound_while_a_task_waits_for_its_probe() -> None:
    tasks = [Task("running", 1_000, 3_000), Task("pending", 0, None)]

    estimate = estimate_eta(tasks, hits_fetched=1_000, elapsed_seconds=10)

    assert estimate.eta_seconds == 20
    assert estimate.is_lower_bound is True


def test_eta_is_unknown_before_speed_or_expectations_exist() -> None:
    early = estimate_eta([Task("running", 5, 100)], hits_fetched=5, elapsed_seconds=2)
    assert early.eta_seconds is None
    waiting = estimate_eta([Task("pending", 0, None)], hits_fetched=0, elapsed_seconds=60)
    assert waiting.eta_seconds is None and waiting.hits_expected is None


def test_eta_is_zero_when_every_task_finished() -> None:
    estimate = estimate_eta([Task("done", 10, 10)], hits_fetched=10, elapsed_seconds=30)

    assert estimate.eta_seconds == 0
    assert estimate.is_lower_bound is False
