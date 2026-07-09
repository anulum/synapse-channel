# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure planning core for the declarative workflow driver
"""The planning brain of the workflow driver, separate from any hub I/O.

A driver advances a compiled workflow against the live board: it posts the tasks,
then on each board change it works out which steps are now ready and routes them
to capable agents. That second part — the planning — is here, as pure functions
over a compiled workflow and a board status map, so it is fully testable without a
running hub. The driver shell (posting, polling, sending assignments) wraps it.

:func:`derive_state` classifies every compiled task as done, in-flight, ready, or
blocked from the board's reported statuses and evidence snapshots (readiness is
recomputed from dependencies and declared evidence, not trusted from a stale stored
status). :func:`plan_assignments` matches ready tasks to capable, free agents within
an in-flight budget — bounded work-handing, never a flood.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES
from synapse_channel.core.workflow import ANY_TERMINAL, CompiledTask

IN_PROGRESS_STATUS = "in_progress"
"""Board status marking a task a worker has actively started."""

DEFAULT_STATUS = "open"
"""Assumed status for a compiled task the board has not reported yet."""

EvidenceSnapshot = Mapping[str, Mapping[str, str]]
"""Evidence values keyed by compiled task id, then predicate name."""


def _edge_satisfied(dep_id: str, required: str, status: Mapping[str, str]) -> str:
    """Classify one dependency edge: ``satisfied``, ``unreachable``, or ``pending``.

    An unconditional edge (``required`` is :data:`ANY_TERMINAL`) is satisfied by any
    terminal status of the dependency. A conditional edge is satisfied only by its
    required terminal status; if the dependency has reached a *different* terminal
    status the edge can never be met (``unreachable``), and otherwise it is still
    ``pending``.
    """
    dep_status = status.get(dep_id, DEFAULT_STATUS)
    dep_terminal = dep_status in TERMINAL_LEDGER_STATUSES
    if required == ANY_TERMINAL:
        return "satisfied" if dep_terminal else "pending"
    if dep_status == required:
        return "satisfied"
    return "unreachable" if dep_terminal else "pending"


def _evidence_satisfied(task: CompiledTask, evidence: EvidenceSnapshot) -> bool:
    """Return whether ``evidence`` proves every predicate required by ``task``."""
    actual = evidence.get(task.task_id, {})
    return all(
        actual.get(predicate, "") == expected for predicate, expected in task.evidence_requirements
    )


@dataclass(frozen=True)
class WorkflowState:
    """A compiled workflow's tasks bucketed by execution phase.

    Attributes
    ----------
    done : tuple[str, ...]
        Task ids in a terminal status (done or cancelled).
    in_flight : tuple[str, ...]
        Task ids a worker has started (in progress).
    ready : tuple[str, ...]
        Not-started task ids whose dependency edges are all satisfied.
    blocked : tuple[str, ...]
        Not-started task ids still waiting on a dependency that may yet be met.
    evidence_blocked : tuple[str, ...]
        Not-started task ids whose dependencies are satisfied but whose declared
        evidence predicates are missing or mismatched.
    skipped : tuple[str, ...]
        Not-started task ids with a conditional edge that can never be met (the
        dependency reached a terminal status other than the one required) — a branch
        not taken. The driver retires these by cancelling them on the board.
    """

    done: tuple[str, ...]
    in_flight: tuple[str, ...]
    ready: tuple[str, ...]
    blocked: tuple[str, ...]
    evidence_blocked: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Return whether nothing is left to run, route, or retire."""
        return not (
            self.in_flight or self.ready or self.blocked or self.evidence_blocked or self.skipped
        )

    def to_dict(self) -> dict[str, list[str]]:
        """Return a JSON-compatible mapping of the phase buckets."""
        return {
            "done": list(self.done),
            "in_flight": list(self.in_flight),
            "ready": list(self.ready),
            "blocked": list(self.blocked),
            "evidence_blocked": list(self.evidence_blocked),
            "skipped": list(self.skipped),
        }


def derive_state(
    tasks: Sequence[CompiledTask],
    status: Mapping[str, str],
    evidence: EvidenceSnapshot | None = None,
) -> WorkflowState:
    """Bucket compiled tasks by phase from a board status map.

    Parameters
    ----------
    tasks : Sequence[CompiledTask]
        The compiled workflow tasks, in dependency order.
    status : Mapping[str, str]
        Board-reported status keyed by task id; unreported tasks are assumed
        :data:`DEFAULT_STATUS`.
    evidence : EvidenceSnapshot or None, optional
        Evidence values keyed by task id, then predicate name. A task declaring
        ``evidence_requirements`` is held in ``evidence_blocked`` until all values
        match.

    Returns
    -------
    WorkflowState
        The tasks bucketed into done, in-flight, ready, blocked, and skipped.
        Readiness is recomputed from the dependency edges, honouring each edge's
        condition: a task is ready only when every edge is satisfied, skipped when an
        edge can never be met (a conditional branch not taken), evidence-blocked
        when dependencies are satisfied but a required proof is absent, and otherwise
        blocked.
    """
    proof = evidence or {}
    done: list[str] = []
    in_flight: list[str] = []
    ready: list[str] = []
    blocked: list[str] = []
    evidence_blocked: list[str] = []
    skipped: list[str] = []
    for task in tasks:
        state = status.get(task.task_id, DEFAULT_STATUS)
        if state in TERMINAL_LEDGER_STATUSES:
            done.append(task.task_id)
        elif state == IN_PROGRESS_STATUS:
            in_flight.append(task.task_id)
        else:
            outcomes = [
                _edge_satisfied(dep_id, task.required_status(dep_id), status)
                for dep_id in task.depends_on
            ]
            if "unreachable" in outcomes:
                skipped.append(task.task_id)
            elif all(outcome == "satisfied" for outcome in outcomes):
                if _evidence_satisfied(task, proof):
                    ready.append(task.task_id)
                else:
                    evidence_blocked.append(task.task_id)
            else:
                blocked.append(task.task_id)
    return WorkflowState(
        done=tuple(done),
        in_flight=tuple(in_flight),
        ready=tuple(ready),
        blocked=tuple(blocked),
        evidence_blocked=tuple(evidence_blocked),
        skipped=tuple(skipped),
    )


@dataclass(frozen=True)
class Assignment:
    """A routing decision: hand one ready task to one capable agent.

    Attributes
    ----------
    task_id : str
        The board task id to assign.
    agent : str
        The agent the task is routed to.
    task_class : str
        The task's routing class (``""`` when unclassified).
    """

    task_id: str
    agent: str
    task_class: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-compatible mapping."""
        return {"task_id": self.task_id, "agent": self.agent, "task_class": self.task_class}


def _capable(agent_classes: frozenset[str], task_class: str) -> bool:
    """Return whether an agent can take a task: it advertises the class, or it is unclassified."""
    return task_class == "" or task_class in agent_classes


def plan_assignments(
    tasks: Sequence[CompiledTask],
    status: Mapping[str, str],
    agents: Mapping[str, frozenset[str]],
    *,
    max_in_flight: int,
    evidence: EvidenceSnapshot | None = None,
) -> tuple[Assignment, ...]:
    """Plan which ready tasks to hand to which agents, within the in-flight budget.

    Parameters
    ----------
    tasks : Sequence[CompiledTask]
        The compiled workflow tasks, in dependency order.
    status : Mapping[str, str]
        Board status keyed by task id.
    agents : Mapping[str, frozenset[str]]
        Available agents mapped to the task classes each advertises.
    max_in_flight : int
        Most tasks allowed in progress at once; clamped up to ``0``.
    evidence : EvidenceSnapshot or None, optional
        Evidence values used to hold proof-carrying tasks until their declared
        predicates match.

    Returns
    -------
    tuple[Assignment, ...]
        Ready tasks paired with a capable, free agent, in dependency order, never
        exceeding the in-flight budget and assigning each agent at most one task.
    """
    state = derive_state(tasks, status, evidence=evidence)
    budget = max(0, max_in_flight) - len(state.in_flight)
    by_id = {task.task_id: task for task in tasks}
    ordered_agents = sorted(agents)
    taken: set[str] = set()
    assignments: list[Assignment] = []
    for task_id in state.ready:
        if budget <= 0:
            break
        task_class = by_id[task_id].task_class
        agent = next(
            (
                name
                for name in ordered_agents
                if name not in taken and _capable(agents[name], task_class)
            ),
            None,
        )
        if agent is None:
            continue
        taken.add(agent)
        assignments.append(Assignment(task_id=task_id, agent=agent, task_class=task_class))
        budget -= 1
    return tuple(assignments)
