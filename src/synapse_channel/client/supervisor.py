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
  change) for longer than the fixed threshold, or a shorter threshold derived
  from completed-task history, is treated as stalled and re-offered;
* a ``blocked`` task whose every dependency has reached a terminal status is a
  stale block and re-offered.

Re-offering sets the task's planning status back to ``open`` so it re-appears in
:meth:`~synapse_channel.core.ledger.Blackboard.ready_tasks` for another agent to pick
up, and records an ``assessment`` progress note explaining why. Because the
re-offer changes the status away from ``in_progress``/``blocked``, the same
stall is not flagged again on the next pass.

:func:`~synapse_channel.core.stall.detect_stalls` is the pure policy — a
function of an observed snapshot and the clock — and :class:`SupervisorWorker`
is the on-channel driver that polls the board and applies what the policy
returns.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.stall import (
    DEFAULT_HISTORY_MULTIPLIER,
    DEFAULT_IDLE_SECONDS,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MIN_HISTORY_SAMPLES,
    DEFAULT_MIN_PREDICTIVE_IDLE_SECONDS,
    Intervention,
    StallPolicy,
    detect_stalls,
)

__all__ = [
    "DEFAULT_HISTORY_MULTIPLIER",
    "DEFAULT_IDLE_SECONDS",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_MIN_HISTORY_SAMPLES",
    "DEFAULT_MIN_PREDICTIVE_IDLE_SECONDS",
    "DEFAULT_SETTLE_SECONDS",
    "Intervention",
    "StallPolicy",
    "SupervisorWorker",
    "detect_stalls",
]

DEFAULT_SETTLE_SECONDS = 0.2
"""Default pause after requesting the board to let the snapshot arrive."""


class SupervisorWorker:
    """An on-channel agent that polls the board and re-offers stalled tasks.

    Parameters
    ----------
    name : str, optional
        Agent name presented on the channel. Defaults to ``"SUPERVISOR"``.
    uri : str, optional
        Hub URI. Defaults to :data:`~synapse_channel.client.agent.DEFAULT_HUB_URI`.
    idle_seconds : float, optional
        Fixed no-activity ceiling passed to :func:`detect_stalls`.
    predictive_stall : bool, optional
        Whether completed-task history may lower the effective no-activity
        threshold.
    history_multiplier : float, optional
        Multiplier applied to the historical median activity gap.
    min_history_samples : int, optional
        Minimum historical gaps required before prediction is used.
    min_predictive_idle_seconds : float, optional
        Floor for the predictive threshold.
    interval : float, optional
        Seconds between passes (floored at 1).
    settle_seconds : float, optional
        Pause after requesting the board to let the snapshot arrive.
    ready_timeout : float, optional
        Seconds to wait for the hub handshake in :meth:`run`. Defaults to ``5.0``.
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
        predictive_stall: bool = True,
        history_multiplier: float = DEFAULT_HISTORY_MULTIPLIER,
        min_history_samples: int = DEFAULT_MIN_HISTORY_SAMPLES,
        min_predictive_idle_seconds: float = DEFAULT_MIN_PREDICTIVE_IDLE_SECONDS,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
        ready_timeout: float = 5.0,
        token: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.name = name
        self.policy = StallPolicy(
            idle_seconds=idle_seconds,
            predictive=predictive_stall,
            history_multiplier=history_multiplier,
            min_history_samples=min_history_samples,
            min_predictive_idle_seconds=min_predictive_idle_seconds,
        )
        self.idle_seconds = self.policy.idle_seconds
        self.interval = max(float(interval), 1.0)
        self.settle_seconds = max(float(settle_seconds), 0.0)
        self.ready_timeout = max(float(ready_timeout), 0.1)
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
        interventions = detect_stalls(self.latest_board, now=self._clock(), policy=self.policy)
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
        if not await self.agent.wait_until_ready(timeout=self.ready_timeout):
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
