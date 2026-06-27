# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stall detection policy for the shared task board
"""Predict stalled board tasks from fixed idleness and local history.

The policy is deliberately local and deterministic. It reads one blackboard
snapshot, never contacts the hub, and returns advisory interventions for the
supervisor process to apply. The fixed idle threshold remains the conservative
operator ceiling; when enabled, completed-task activity cadence can lower that
ceiling for repositories whose recent board history shows faster normal progress.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES

DEFAULT_IDLE_SECONDS = 300.0
"""Default no-activity window after which an in-progress task is re-offered."""

DEFAULT_INTERVAL_SECONDS = 30.0
"""Default seconds between supervisor passes."""

DEFAULT_HISTORY_MULTIPLIER = 3.0
"""Default multiplier applied to historical median activity cadence."""

DEFAULT_MIN_HISTORY_SAMPLES = 4
"""Default minimum historical activity gaps before predictive cadence is used."""

DEFAULT_MIN_PREDICTIVE_IDLE_SECONDS = 60.0
"""Default floor below which predictive idleness never re-offers a task."""

DEFAULT_HISTORY_TASK_LIMIT = 25
"""Default number of recent terminal tasks used for historical cadence."""


@dataclass(frozen=True)
class Intervention:
    """A single action the supervisor decided to take on a task.

    Attributes
    ----------
    task_id : str
        The task the intervention concerns.
    action : str
        What to do; currently always ``"reoffer"``.
    reason : str
        Human-readable explanation, recorded with the re-offer.
    """

    task_id: str
    action: str
    reason: str


@dataclass(frozen=True)
class StallPolicy:
    """Operator-tunable policy for stall detection.

    Parameters
    ----------
    idle_seconds : float, optional
        Fixed no-activity ceiling for in-progress tasks. Values below one second
        are clamped to one second.
    predictive : bool, optional
        Whether completed-task history may lower the effective in-progress
        threshold. Defaults to ``True``.
    history_multiplier : float, optional
        Multiplier applied to the median historical activity gap. Values below
        one are clamped to one.
    min_history_samples : int, optional
        Minimum number of historical gaps required before prediction is used.
        Values below one are clamped to one.
    min_predictive_idle_seconds : float, optional
        Floor for the predictive threshold. Values below one second are clamped
        to one second.
    history_task_limit : int, optional
        Number of recent terminal tasks to inspect for cadence. Values below one
        are clamped to one.
    """

    idle_seconds: float = DEFAULT_IDLE_SECONDS
    predictive: bool = True
    history_multiplier: float = DEFAULT_HISTORY_MULTIPLIER
    min_history_samples: int = DEFAULT_MIN_HISTORY_SAMPLES
    min_predictive_idle_seconds: float = DEFAULT_MIN_PREDICTIVE_IDLE_SECONDS
    history_task_limit: int = DEFAULT_HISTORY_TASK_LIMIT

    def __post_init__(self) -> None:
        """Clamp policy values so the detector has no invalid runtime states."""
        object.__setattr__(self, "idle_seconds", max(float(self.idle_seconds), 1.0))
        object.__setattr__(self, "history_multiplier", max(float(self.history_multiplier), 1.0))
        object.__setattr__(self, "min_history_samples", max(int(self.min_history_samples), 1))
        object.__setattr__(
            self,
            "min_predictive_idle_seconds",
            max(float(self.min_predictive_idle_seconds), 1.0),
        )
        object.__setattr__(self, "history_task_limit", max(int(self.history_task_limit), 1))


@dataclass(frozen=True)
class _Threshold:
    """Effective in-progress idle threshold plus the reason suffix."""

    seconds: float
    predictive: bool = False

    def reason(self) -> str:
        """Return the human-readable threshold reason."""
        suffix = " (historical cadence)" if self.predictive else ""
        return f"no progress in {_format_seconds(self.seconds)}{suffix}"


def detect_stalls(
    board: dict[str, Any],
    *,
    now: float,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
    policy: StallPolicy | None = None,
) -> list[Intervention]:
    """Decide which tasks on a board snapshot should be re-offered.

    Parameters
    ----------
    board : dict[str, Any]
        A blackboard snapshot as returned by
        :meth:`~synapse_channel.core.ledger.Blackboard.snapshot`.
    now : float
        Current wall-clock time, in seconds, used to age in-progress tasks.
    idle_seconds : float, optional
        Backwards-compatible fixed no-activity window. Ignored when ``policy`` is
        supplied.
    policy : StallPolicy or None, optional
        Full operator policy. ``None`` preserves the historical fixed-threshold
        call shape.

    Returns
    -------
    list[Intervention]
        One re-offer per stalled task, sorted by ``task_id``.
    """
    active_policy = StallPolicy(idle_seconds=idle_seconds) if policy is None else policy
    tasks = [task for task in board.get("tasks", []) if isinstance(task, dict)]
    by_id = {str(task.get("task_id", "")): task for task in tasks}
    progress_by_task = _progress_by_task(board.get("progress", []))
    threshold = _effective_threshold(tasks, progress_by_task, active_policy)

    interventions: list[Intervention] = []
    for task in tasks:
        task_id = str(task.get("task_id", ""))
        status = task.get("status")
        if status == "in_progress":
            idle = float(now) - _latest_activity(task, progress_by_task)
            if idle >= threshold.seconds:
                interventions.append(Intervention(task_id, "reoffer", threshold.reason()))
        elif status == "blocked" and _dependencies_satisfied(task, by_id):
            interventions.append(Intervention(task_id, "reoffer", "dependencies satisfied"))
    return sorted(interventions, key=lambda item: item.task_id)


def _progress_by_task(progress: object) -> dict[str, list[float]]:
    """Group valid progress timestamps by task id."""
    grouped: dict[str, list[float]] = defaultdict(list)
    if not isinstance(progress, list):
        return grouped
    for note in progress:
        if not isinstance(note, dict):
            continue
        task_id = str(note.get("task_id", ""))
        grouped[task_id].append(_as_float(note.get("posted_at"), 0.0))
    return grouped


def _latest_activity(task: dict[str, Any], progress_by_task: dict[str, list[float]]) -> float:
    """Return the most recent activity time for a task."""
    times = [_as_float(task.get("updated_at"), 0.0)]
    times.extend(progress_by_task.get(str(task.get("task_id", "")), []))
    return max(times)


def _dependencies_satisfied(task: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    """Return whether every declared dependency has reached a terminal status."""
    return all(
        by_id.get(str(dep), {}).get("status") in TERMINAL_LEDGER_STATUSES
        for dep in task.get("depends_on", [])
    )


def _effective_threshold(
    tasks: Sequence[dict[str, Any]],
    progress_by_task: dict[str, list[float]],
    policy: StallPolicy,
) -> _Threshold:
    """Return the active in-progress idle threshold."""
    fixed = _Threshold(policy.idle_seconds)
    if not policy.predictive:
        return fixed
    gaps = _historical_activity_gaps(tasks, progress_by_task, policy.history_task_limit)
    if len(gaps) < policy.min_history_samples:
        return fixed
    cadence = float(statistics.median(gaps)) * policy.history_multiplier
    predictive_seconds = max(policy.min_predictive_idle_seconds, cadence)
    if predictive_seconds >= policy.idle_seconds:
        return fixed
    return _Threshold(predictive_seconds, predictive=True)


def _historical_activity_gaps(
    tasks: Sequence[dict[str, Any]],
    progress_by_task: dict[str, list[float]],
    task_limit: int,
) -> tuple[float, ...]:
    """Return positive activity gaps from recent terminal tasks."""
    terminal = [
        task
        for task in tasks
        if task.get("status") in TERMINAL_LEDGER_STATUSES and str(task.get("task_id", ""))
    ]
    terminal.sort(key=lambda task: _as_float(task.get("updated_at"), 0.0), reverse=True)
    gaps: list[float] = []
    for task in terminal[:task_limit]:
        gaps.extend(_activity_gaps(_activity_times(task, progress_by_task)))
    return tuple(gaps)


def _activity_times(
    task: dict[str, Any], progress_by_task: dict[str, list[float]]
) -> tuple[float, ...]:
    """Return sorted unique timestamps that describe a task's activity cadence."""
    task_id = str(task.get("task_id", ""))
    raw = [
        _as_float(task.get("created_at"), 0.0),
        *progress_by_task.get(task_id, []),
        _as_float(task.get("updated_at"), 0.0),
    ]
    return tuple(sorted(dict.fromkeys(stamp for stamp in raw if stamp > 0.0)))


def _activity_gaps(times: Sequence[float]) -> tuple[float, ...]:
    """Return positive adjacent gaps from sorted activity timestamps."""
    return tuple(
        gap for left, right in zip(times, times[1:], strict=False) if (gap := right - left) > 0.0
    )


def _as_float(value: object, default: float) -> float:
    """Coerce a timestamp-like value, returning ``default`` when invalid."""
    if not isinstance(value, str | int | float):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _format_seconds(seconds: float) -> str:
    """Format seconds for stable human-readable reasons."""
    if seconds.is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:.3f}s"
