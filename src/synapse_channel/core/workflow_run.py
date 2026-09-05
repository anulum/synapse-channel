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

The deadline is one budget spanning declaration, every board reading and every
board write: no operation starts once it has passed, each gateway await is bounded
by the time left, and the sleep between readings never outlives it. An operation
that was still in flight when the budget ran out is reported as *interrupted* — its
effect on the board is unknown and no rollback is promised. When no board reading
completed, the result carries no state rather than an invented empty one.

The loop is written against a small :class:`WorkflowGateway` Protocol — three
coroutines for posting tasks, reading the board, and assigning an owner — and an
injected clock and sleep. That keeps it fully testable over an in-memory fake board
with a virtual clock, with no running hub; the CLI supplies a hub-backed gateway.
Assignment is idempotent: a task already carrying the chosen owner is left alone, so
re-reading an unchanged board issues no redundant writes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Protocol, TypeVar

from synapse_channel.core.workflow import CompiledTask
from synapse_channel.core.workflow_driver import (
    EvidenceSnapshot,
    WorkflowState,
    derive_state,
    plan_assignments,
)

_T = TypeVar("_T")

_TIMEOUTS: tuple[type[BaseException], ...] = (asyncio.TimeoutError, TimeoutError)
"""Both timeout classes: they are one class on Python 3.11+, distinct on 3.10."""


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
    evidence : EvidenceSnapshot
        Evidence values keyed by task id, then predicate. A task with declared
        evidence requirements is not assigned until these values match.
    """

    status: Mapping[str, str]
    suggested_owner: Mapping[str, str]
    evidence: EvidenceSnapshot = field(default_factory=dict)


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

    async def cancel(self, task_id: str) -> None:
        """Retire ``task_id`` on the board (a conditional branch that was not taken)."""


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
    cancellations : tuple[str, ...]
        Every task id the loop retired (a conditional branch not taken), in order.
    state : WorkflowState or None
        The phase buckets derived from the final completed board reading, or
        ``None`` when the deadline passed before any reading completed — the board
        was never observed, so no state is invented.
    interrupted : tuple[str, ...]
        Gateway operations still in flight when the budget ran out, in order:
        ``post_tasks``, ``read_board``, ``assign:<task_id>:<agent>`` or
        ``cancel:<task_id>``. Their effect on the board is unknown; an interrupted
        write is not counted in ``assignments`` or ``cancellations`` and is not
        rolled back.
    """

    complete: bool
    timed_out: bool
    polls: int
    assignments: tuple[tuple[str, str], ...]
    cancellations: tuple[str, ...]
    state: WorkflowState | None
    interrupted: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible summary of the run."""
        return {
            "complete": self.complete,
            "timed_out": self.timed_out,
            "polls": self.polls,
            "assignments": [{"task_id": tid, "agent": agent} for tid, agent in self.assignments],
            "cancellations": list(self.cancellations),
            "state": None if self.state is None else self.state.to_dict(),
            "interrupted": list(self.interrupted),
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

    The deadline is checked before every gateway operation and before every sleep,
    each gateway await is bounded by the remaining budget, and the sleep is
    shortened to the remaining budget. An already-expired deadline therefore posts
    and writes nothing; a deadline that passes during a reading produces no further
    writes; an operation cut off mid-flight is listed in ``RunResult.interrupted``.

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
        written, the operations interrupted by the deadline, and the final derived
        state (``None`` when no board reading completed).
    """
    written: list[tuple[str, str]] = []
    retired: list[str] = []
    interrupted: list[str] = []
    polls = 0
    state: WorkflowState | None = None

    def stopped(*, complete: bool) -> RunResult:
        return RunResult(
            complete=complete,
            timed_out=not complete,
            polls=polls,
            assignments=tuple(written),
            cancellations=tuple(retired),
            state=state,
            interrupted=tuple(interrupted),
        )

    async def within_budget(
        label: str, operation: Callable[[], Awaitable[_T]]
    ) -> tuple[bool, _T | None]:
        """Run one gateway operation inside the remaining budget.

        Returns ``(True, value)`` when it completed, ``(False, None)`` when the
        budget was already spent (nothing started) or ran out mid-flight (then
        ``label`` is recorded as interrupted).
        """
        budget = deadline - clock()
        if budget <= 0:
            return False, None
        try:
            return True, await asyncio.wait_for(operation(), timeout=budget)
        except _TIMEOUTS:
            interrupted.append(label)
            return False, None

    posted, _ = await within_budget("post_tasks", partial(gateway.post_tasks, tasks))
    if not posted:
        return stopped(complete=False)
    while True:
        read, snapshot = await within_budget("read_board", gateway.read_board)
        if not read or snapshot is None:
            return stopped(complete=False)
        polls += 1
        state = derive_state(tasks, snapshot.status, evidence=snapshot.evidence)
        if state.complete:
            return stopped(complete=True)
        for task_id in state.skipped:
            cancelled, _ = await within_budget(
                f"cancel:{task_id}", partial(gateway.cancel, task_id)
            )
            if not cancelled:
                return stopped(complete=False)
            retired.append(task_id)
        for assignment in plan_assignments(
            tasks,
            snapshot.status,
            agents,
            max_in_flight=max_in_flight,
            evidence=snapshot.evidence,
        ):
            if snapshot.suggested_owner.get(assignment.task_id, "") == assignment.agent:
                continue
            assigned, _ = await within_budget(
                f"assign:{assignment.task_id}:{assignment.agent}",
                partial(gateway.assign, assignment.task_id, assignment.agent),
            )
            if not assigned:
                return stopped(complete=False)
            written.append((assignment.task_id, assignment.agent))
        budget = deadline - clock()
        if budget <= 0:
            return stopped(complete=False)
        await sleep(min(poll_interval, budget))
