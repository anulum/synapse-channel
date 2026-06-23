# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — LLM-free supervisor that spots stalls and re-offers tasks
"""A rule-based supervisor that watches the plan and re-offers stalled work.

The supervisor is an ordinary agent that observes the shared blackboard and
nudges it when work gets stuck — with no model in the default path, so it is
deterministic and cheap to run. It applies two rules over a board snapshot:

* an ``in_progress`` task with no activity (no progress note and no status
  change) for longer than an idle threshold is treated as stalled and
  re-offered;
* a ``blocked`` task whose every dependency has reached a terminal status is a
  stale block and re-offered.

Re-offering sets the task's planning status back to ``open`` so it re-appears in
:meth:`~synapse_channel.core.ledger.Blackboard.ready_tasks` for another agent to pick
up, and records an ``assessment`` progress note explaining why. Because the
re-offer changes the status away from ``in_progress``/``blocked``, the same
stall is not flagged again on the next pass.

:func:`detect_stalls` is the pure policy — a function of an observed snapshot and
the clock — and :class:`SupervisorWorker` is the on-channel driver that polls the
board and applies what the policy returns.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES
from synapse_channel.core.protocol import MessageType

DEFAULT_IDLE_SECONDS = 300.0
"""Default no-activity window after which an in-progress task is re-offered."""

DEFAULT_INTERVAL_SECONDS = 30.0
"""Default seconds between supervisor passes."""

DEFAULT_SETTLE_SECONDS = 0.2
"""Default pause after requesting the board to let the snapshot arrive."""


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


def _latest_activity(task: dict[str, Any], progress_by_task: dict[str, list[float]]) -> float:
    """Return the most recent activity time for a task (update or progress)."""
    times = [float(task.get("updated_at", 0.0))]
    times.extend(progress_by_task.get(str(task.get("task_id", "")), []))
    return max(times)


def _dependencies_satisfied(task: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    """Return whether every declared dependency has reached a terminal status."""
    return all(
        by_id.get(str(dep), {}).get("status") in TERMINAL_LEDGER_STATUSES
        for dep in task.get("depends_on", [])
    )


def detect_stalls(
    board: dict[str, Any], *, now: float, idle_seconds: float = DEFAULT_IDLE_SECONDS
) -> list[Intervention]:
    """Decide which tasks on a board snapshot are stalled and should be re-offered.

    Parameters
    ----------
    board : dict[str, Any]
        A blackboard snapshot as returned by
        :meth:`~synapse_channel.core.ledger.Blackboard.snapshot`.
    now : float
        Current wall-clock time, in seconds, used to age in-progress tasks.
    idle_seconds : float, optional
        No-activity window after which an ``in_progress`` task is stalled.

    Returns
    -------
    list[Intervention]
        One re-offer per stalled task, sorted by ``task_id``.
    """
    tasks = list(board.get("tasks", []))
    by_id = {str(task.get("task_id", "")): task for task in tasks}
    progress_by_task: dict[str, list[float]] = defaultdict(list)
    for note in board.get("progress", []):
        progress_by_task[str(note.get("task_id", ""))].append(float(note.get("posted_at", 0.0)))

    interventions: list[Intervention] = []
    for task in tasks:
        task_id = str(task.get("task_id", ""))
        status = task.get("status")
        if status == "in_progress":
            idle = now - _latest_activity(task, progress_by_task)
            if idle >= idle_seconds:
                interventions.append(
                    Intervention(task_id, "reoffer", f"no progress in {int(idle_seconds)}s")
                )
        elif status == "blocked" and _dependencies_satisfied(task, by_id):
            interventions.append(Intervention(task_id, "reoffer", "dependencies satisfied"))
    return sorted(interventions, key=lambda item: item.task_id)


class SupervisorWorker:
    """An on-channel agent that polls the board and re-offers stalled tasks.

    Parameters
    ----------
    name : str, optional
        Agent name presented on the channel. Defaults to ``"SUPERVISOR"``.
    uri : str, optional
        Hub URI. Defaults to :data:`~synapse_channel.client.agent.DEFAULT_HUB_URI`.
    idle_seconds : float, optional
        No-activity window passed to :func:`detect_stalls`.
    interval : float, optional
        Seconds between passes (floored at 1).
    settle_seconds : float, optional
        Pause after requesting the board to let the snapshot arrive.
    token : str or None, optional
        Shared-secret token for a secured hub.
    clock : Callable[[], float], optional
        Wall-clock source; injectable for deterministic tests.
    """

    def __init__(
        self,
        *,
        name: str = "SUPERVISOR",
        uri: str = DEFAULT_HUB_URI,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
        token: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.name = name
        self.idle_seconds = idle_seconds
        self.interval = max(float(interval), 1.0)
        self.settle_seconds = max(float(settle_seconds), 0.0)
        self._clock = clock
        self.latest_board: dict[str, Any] | None = None
        self.agent = SynapseAgent(name, on_message_callback=self.on_message, uri=uri, token=token)

    async def on_message(self, data: dict[str, Any]) -> None:
        """Capture the latest board snapshot the hub sends back."""
        if data.get("type") == MessageType.BOARD_SNAPSHOT:
            self.latest_board = dict(data.get("board", {}))

    async def evaluate_and_apply(self) -> list[Intervention]:
        """Run the stall policy on the latest board and apply each re-offer.

        Returns
        -------
        list[Intervention]
            The interventions applied (empty when no board has arrived yet or
            nothing is stalled).
        """
        if self.latest_board is None:
            return []
        interventions = detect_stalls(
            self.latest_board, now=self._clock(), idle_seconds=self.idle_seconds
        )
        for item in interventions:
            await self.agent.post_progress(
                item.task_id, f"supervisor: {item.reason}; re-offering", kind="assessment"
            )
            await self.agent.update_ledger_task(item.task_id, status="open")
        return interventions

    async def _cycle(self) -> list[Intervention]:
        """Request a fresh board, let it settle, then evaluate and apply."""
        await self.agent.request_board()
        if self.settle_seconds:
            await asyncio.sleep(self.settle_seconds)
        return await self.evaluate_and_apply()

    async def _supervise_loop(self) -> None:
        """Run a supervision pass every ``interval`` seconds while connected."""
        while self.agent.running:
            await self._cycle()
            await asyncio.sleep(self.interval)

    async def run(self) -> None:
        """Connect, wait for the handshake, and supervise until the link ends."""
        conn_task = asyncio.create_task(self.agent.connect())
        if not await self.agent.wait_until_ready(timeout=5.0):
            print(f"[{self.name}] Warning: handshake timeout.")
        supervise_task = asyncio.create_task(self._supervise_loop())
        done, pending = await asyncio.wait(
            {conn_task, supervise_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            try:
                task.result()
            except Exception as exc:
                print(f"[{self.name}] supervisor stopped: {exc}")
