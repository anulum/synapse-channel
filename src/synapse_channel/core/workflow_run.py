# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the workflow driver's live advance loop, separate from hub I/O
"""The live advance loop that drives a compiled workflow against the board.

The planning brain (:mod:`synapse_channel.core.workflow_driver`) is pure: given a
board status map it says which steps are ready and which agent each should go to.
This module is the loop that *applies* that brain to a live board over time — post
the tasks once, then on every board reading re-derive the state, route the ready
steps to capable agents by writing each task's ``suggested_owner`` (advisory, never
forced), and stop when the workflow is complete or a deadline passes.

The loop is written against a small :class:`WorkflowGateway` Protocol — three
coroutines for posting tasks, reading the board, and assigning an owner — and an
injected clock and sleep. That keeps it fully testable over an in-memory fake board
with a virtual clock, with no running hub; the CLI supplies a hub-backed gateway.
Assignment is idempotent: a task already carrying the chosen owner is left alone, so
re-reading an unchanged board issues no redundant writes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from synapse_channel.core.workflow import CompiledTask
from synapse_channel.core.workflow_driver import WorkflowState, derive_state, plan_assignments


@dataclass(frozen=True)
class BoardSnapshot:
    """A board reading reduced to what the driver routes on.

    Attributes
    ----------
    status : Mapping[str, str]
        Planning status keyed by task id (``open``/``in_progress``/``done``/…).
    suggested_owner : Mapping[str, str]
        The owner currently advised for each task id; absent or ``""`` means none.
        Used to make assignment idempotent — a task already advising the chosen
        agent is not re-assigned.
    """

    status: Mapping[str, str]
    suggested_owner: Mapping[str, str]


class WorkflowGateway(Protocol):
    """The three board operations the live loop needs, abstracted from transport.

    A real implementation wraps a connected hub client; the tests supply an
    in-memory board. Keeping the loop to this surface is what makes it testable
    without a running hub.
    """

    async def post_tasks(self, tasks: Sequence[CompiledTask]) -> None:
        """Declare every compiled task on the board (idempotent re-declare)."""

    async def read_board(self) -> BoardSnapshot:
        """Return the current board reading."""

    async def assign(self, task_id: str, agent: str) -> None:
        """Advise ``agent`` as the owner of ``task_id`` on the board."""


@dataclass(frozen=True)
class RunResult:
    """The outcome of a driver run.

    Attributes
    ----------
    complete : bool
        Whether every task reached a terminal status before the loop stopped.
    timed_out : bool
        Whether the loop stopped because the deadline passed rather than because
        the workflow completed.
    polls : int
        How many board readings the loop took.
    assignments : tuple[tuple[str, str], ...]
        Every ``(task_id, agent)`` the loop wrote, in order, across all polls.
    state : WorkflowState
        The phase buckets derived from the final board reading.
    """

    complete: bool
    timed_out: bool
    polls: int
    assignments: tuple[tuple[str, str], ...]
    state: WorkflowState

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible summary of the run."""
        return {
            "complete": self.complete,
            "timed_out": self.timed_out,
            "polls": self.polls,
            "assignments": [{"task_id": tid, "agent": agent} for tid, agent in self.assignments],
            "state": self.state.to_dict(),
        }


async def run_workflow(
    tasks: Sequence[CompiledTask],
    agents: Mapping[str, frozenset[str]],
    gateway: WorkflowGateway,
    *,
    max_in_flight: int,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    poll_interval: float,
) -> RunResult:
    """Drive a compiled workflow against a live board until complete or past deadline.

    The loop posts the tasks once, then repeatedly reads the board, derives the
    phase state, and — while work remains — routes the ready steps to capable free
    agents (bounded by ``max_in_flight``) by advising each chosen owner. It returns
    as soon as every task is terminal, or once ``clock()`` reaches ``deadline``.

    Parameters
    ----------
    tasks : Sequence[CompiledTask]
        The compiled workflow tasks, in dependency order.
    agents : Mapping[str, frozenset[str]]
        Candidate agents mapped to the task classes each advertises.
    gateway : WorkflowGateway
        Board operations (post, read, assign).
    max_in_flight : int
        Most tasks allowed in progress at once; the planner clamps it up to ``0``.
    deadline : float
        Absolute ``clock()`` value at which to stop if not yet complete.
    clock : Callable[[], float]
        Monotonic time source, compared against ``deadline``.
    sleep : Callable[[float], Awaitable[None]]
        Awaitable delay between board readings.
    poll_interval : float
        Seconds to ``sleep`` between readings.

    Returns
    -------
    RunResult
        Completion flag, whether it timed out, the poll count, every assignment
        written, and the final derived state.
    """
    await gateway.post_tasks(tasks)
    written: list[tuple[str, str]] = []
    polls = 0
    while True:
        snapshot = await gateway.read_board()
        polls += 1
        state = derive_state(tasks, snapshot.status)
        if state.complete:
            return RunResult(
                complete=True,
                timed_out=False,
                polls=polls,
                assignments=tuple(written),
                state=state,
            )
        for assignment in plan_assignments(
            tasks, snapshot.status, agents, max_in_flight=max_in_flight
        ):
            if snapshot.suggested_owner.get(assignment.task_id, "") == assignment.agent:
                continue
            await gateway.assign(assignment.task_id, assignment.agent)
            written.append((assignment.task_id, assignment.agent))
        if clock() >= deadline:
            return RunResult(
                complete=False,
                timed_out=True,
                polls=polls,
                assignments=tuple(written),
                state=state,
            )
        await sleep(poll_interval)
