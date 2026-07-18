# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deterministic ready-task dispatch selection (pure)
"""Pure, deterministic selection of ready tasks for dispatchable project seats.

This module decides *who should be nudged for which ready task* and nothing
else: it reads plain snapshot data (board tasks, claims, capability cards,
online roster, wake capabilities) and returns an auditable plan. It holds no
connection, performs no mutation, and grants no authority — the dispatcher
client executes the plan through the ordinary wire verbs, and the woken agent
still claims and updates the task itself.

Eligibility rules (fail-closed defaults):

* A task is dispatchable only when it is ready (open with met dependencies),
  its ``project`` scope exactly equals the dispatcher's project (an unscoped
  task is never auto-dispatched), it holds no active claim, and its
  ``suggested_owner`` is absent or stale (no claim observed within
  ``suggestion_ttl``).
* A candidate is dispatchable only when its card belongs to the project, it
  has not opted out (``dispatchable: false``), the seat (or its ``-rx``
  sidecar) is online with a ``direct`` or ``pane_bridge`` wake capability,
  and it holds fewer active claims than ``capacity``.

Ranking is a total order, so two dispatchers with the same inputs always
agree: class-hint match first, then wake rank (pane bridge beats direct),
then longer-idle registration, then the agent name. A peer receives at most
``capacity - active_claims`` assignments per pass.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.wake_capability import WAKE_DIRECT, WAKE_PANE_BRIDGE

DISPATCH_TRUST_BOUNDARY = (
    "Dispatch assignments are advisory nudges: the dispatcher never claims, "
    "approves, or lands work; the woken agent claims and updates the task itself."
)
"""Trust boundary carried by every rendered dispatch plan."""

DEFAULT_SUGGESTION_TTL_SECONDS = 900.0
"""Age after which an un-claimed ``suggested_owner`` assignment is re-opened."""

_WAKE_RANK = {WAKE_PANE_BRIDGE: 2, WAKE_DIRECT: 1}


@dataclass(frozen=True)
class DispatchTask:
    """A normalized board task considered for dispatch.

    Attributes
    ----------
    task_id : str
        Board identifier.
    project : str
        Scope namespace; ``""`` means unscoped (never dispatchable).
    suggested_owner : str
        Current advisory owner, if any.
    updated_at : float
        Wall-clock seconds of the last board mutation (drives staleness and
        idempotency keys downstream).
    version : int
        Monotonic board version (used for CAS by the dispatcher client).
    """

    task_id: str
    project: str
    suggested_owner: str
    updated_at: float
    version: int


@dataclass(frozen=True)
class DispatchCandidate:
    """A normalized dispatchable seat.

    Attributes
    ----------
    agent : str
        Seat identity from its capability card.
    wake_identity : str
        Online identity that can be woken (the seat or its ``-rx`` sidecar).
    wake_capability : str
        ``direct`` or ``pane_bridge``.
    task_classes : tuple[str, ...]
        Advertised routing classes.
    skills : tuple[str, ...]
        Advertised skill tags.
    active_claims : int
        Currently held claims (capacity input).
    advertised_at : float
        Card refresh time; older means longer-idle.
    """

    agent: str
    wake_identity: str
    wake_capability: str
    task_classes: tuple[str, ...]
    skills: tuple[str, ...]
    active_claims: int
    advertised_at: float


@dataclass(frozen=True)
class DispatchAssignment:
    """One planned task → seat nudge with an auditable rationale.

    Attributes
    ----------
    task_id : str
        Task to nudge for.
    owner : str
        Seat to nudge.
    wake_identity : str
        Online identity the wake message targets.
    class_score : int
        ``1`` when a task-id token matched an advertised class or skill.
    reasons : tuple[str, ...]
        Human-auditable explanation of the decision.
    """

    task_id: str
    owner: str
    wake_identity: str
    class_score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DispatchPlan:
    """The deterministic result of one selection pass.

    Attributes
    ----------
    assignments : tuple[DispatchAssignment, ...]
        Planned nudges, ordered by task id.
    skipped : dict[str, tuple[str, ...]]
        Per ready task that was not assigned, the reasons why.
    trust_boundary : str
        Advisory boundary text consumers must render with the plan.
    """

    assignments: tuple[DispatchAssignment, ...]
    skipped: dict[str, tuple[str, ...]] = field(default_factory=dict)
    trust_boundary: str = DISPATCH_TRUST_BOUNDARY


def _task_hint_tokens(task_id: str) -> frozenset[str]:
    """Split a task id into lowercase hint tokens for the class heuristic."""
    return frozenset(token for token in task_id.replace("_", "-").lower().split("-") if token)


def _normalize_tasks(tasks: list[Mapping[str, Any]]) -> list[DispatchTask]:
    """Keep only open tasks and normalize their fields."""
    normalized: list[DispatchTask] = []
    for raw in tasks:
        if str(raw.get("status") or "") != "open":
            continue
        task_id = str(raw.get("task_id") or "").strip()
        if not task_id:
            continue
        updated = raw.get("updated_at")
        version = raw.get("version")
        normalized.append(
            DispatchTask(
                task_id=task_id,
                project=str(raw.get("project") or "").strip(),
                suggested_owner=str(raw.get("suggested_owner") or "").strip(),
                updated_at=float(updated) if isinstance(updated, (int, float)) else 0.0,
                version=int(version) if isinstance(version, int) and version > 0 else 1,
            )
        )
    return normalized


def _normalize_cards(
    cards: list[Mapping[str, Any]],
    *,
    project: str,
    online: frozenset[str],
    wake_capabilities: Mapping[str, str],
    claims_per_agent: Mapping[str, int],
) -> list[DispatchCandidate]:
    """Filter and normalize dispatchable candidates for ``project``."""
    candidates: list[DispatchCandidate] = []
    for raw in cards:
        agent = str(raw.get("agent") or "").strip()
        if not agent or (agent != project and not agent.startswith(f"{project}/")):
            continue
        if raw.get("dispatchable") is False:
            continue
        wake_identity = ""
        wake_capability = ""
        for identity in (agent, f"{agent}-rx"):
            capability = wake_capabilities.get(identity, "")
            if identity in online and capability in _WAKE_RANK:
                wake_identity, wake_capability = identity, capability
                break
        if not wake_identity:
            continue
        raw_classes = raw.get("task_classes")
        raw_skills = raw.get("skills")
        advertised = raw.get("advertised_at")
        candidates.append(
            DispatchCandidate(
                agent=agent,
                wake_identity=wake_identity,
                wake_capability=wake_capability,
                task_classes=tuple(str(c) for c in raw_classes)
                if isinstance(raw_classes, list)
                else (),
                skills=tuple(str(s) for s in raw_skills) if isinstance(raw_skills, list) else (),
                active_claims=claims_per_agent.get(agent, 0),
                advertised_at=float(advertised) if isinstance(advertised, (int, float)) else 0.0,
            )
        )
    return candidates


def plan_dispatches(
    *,
    tasks: list[Mapping[str, Any]],
    ready_ids: frozenset[str],
    claims: list[Mapping[str, Any]],
    cards: list[Mapping[str, Any]],
    online: frozenset[str],
    wake_capabilities: Mapping[str, str],
    project: str,
    now: float | None = None,
    suggestion_ttl: float = DEFAULT_SUGGESTION_TTL_SECONDS,
    capacity: int = 1,
) -> DispatchPlan:
    """Compute a deterministic dispatch plan for one project pass.

    Parameters
    ----------
    tasks : list[Mapping[str, Any]]
        Board task bodies from the board snapshot.
    ready_ids : frozenset[str]
        Ready task ids from the board snapshot.
    claims : list[Mapping[str, Any]]
        Active claims from the state snapshot (``task_id`` + ``owner``).
    cards : list[Mapping[str, Any]]
        Manifest cards (live and persistent).
    online : frozenset[str]
        Currently online identity names.
    wake_capabilities : Mapping[str, str]
        Per-identity wake capability tokens from the who snapshot.
    project : str
        The dispatcher's exact project scope.
    now : float or None, optional
        Wall-clock override for deterministic tests.
    suggestion_ttl : float, optional
        Seconds after which an un-claimed suggestion is re-opened.
    capacity : int, optional
        Maximum active claims per seat; a seat receives at most
        ``capacity - active_claims`` assignments this pass.

    Returns
    -------
    DispatchPlan
        Deterministic assignments plus per-task skip reasons.
    """
    ts = time.time() if now is None else float(now)
    claims_per_agent: dict[str, int] = {}
    claimed_tasks: set[str] = set()
    for claim in claims:
        task_id = str(claim.get("task_id") or "").strip()
        owner = str(claim.get("owner") or "").strip()
        if task_id:
            claimed_tasks.add(task_id)
        if owner:
            claims_per_agent[owner] = claims_per_agent.get(owner, 0) + 1

    candidates = _normalize_cards(
        cards,
        project=project,
        online=online,
        wake_capabilities=wake_capabilities,
        claims_per_agent=claims_per_agent,
    )
    candidates.sort(
        key=lambda candidate: (
            -_WAKE_RANK[candidate.wake_capability],
            -(ts - candidate.advertised_at),
            candidate.agent,
        )
    )

    assignments: list[DispatchAssignment] = []
    skipped: dict[str, tuple[str, ...]] = {}
    planned_per_agent: dict[str, int] = {}
    eligible_tasks = [task for task in _normalize_tasks(tasks) if task.task_id in ready_ids]
    eligible_tasks.sort(key=lambda task: task.task_id)

    for task in eligible_tasks:
        if task.project != project:
            skipped[task.task_id] = (
                ("task is unscoped" if not task.project else f"task scoped to {task.project}"),
            )
            continue
        if task.task_id in claimed_tasks:
            skipped[task.task_id] = ("an active claim already covers the task",)
            continue
        if task.suggested_owner and (ts - task.updated_at) < suggestion_ttl:
            skipped[task.task_id] = (
                f"fresh suggestion for {task.suggested_owner} "
                f"({int(ts - task.updated_at)}s < {int(suggestion_ttl)}s TTL)",
            )
            continue

        tokens = _task_hint_tokens(task.task_id)
        best: tuple[int, DispatchCandidate] | None = None
        for candidate in candidates:
            if candidate.active_claims + planned_per_agent.get(candidate.agent, 0) >= capacity:
                continue
            classes = frozenset(item.lower() for item in candidate.task_classes)
            skills = frozenset(item.lower() for item in candidate.skills)
            class_score = int(bool(tokens & classes or tokens & skills))
            # ``candidates`` is pre-sorted by (wake rank, idle, agent), so the
            # first candidate holding the best class score wins deterministically.
            if best is None or class_score > best[0]:
                best = (class_score, candidate)
        if best is None:
            skipped[task.task_id] = (
                "no dispatchable candidate (project/online/wake/capacity gate)",
            )
            continue

        class_score, winner = best
        planned_per_agent[winner.agent] = planned_per_agent.get(winner.agent, 0) + 1
        reasons = [
            f"wake {winner.wake_capability} via {winner.wake_identity}",
            f"class hint {'matched' if class_score else 'no match'}",
            f"idle {int(ts - winner.advertised_at)}s since card refresh",
        ]
        if task.suggested_owner:
            reasons.append(f"stale suggestion for {task.suggested_owner} re-opened")
        assignments.append(
            DispatchAssignment(
                task_id=task.task_id,
                owner=winner.agent,
                wake_identity=winner.wake_identity,
                class_score=class_score,
                reasons=tuple(reasons),
            )
        )

    return DispatchPlan(assignments=tuple(assignments), skipped=skipped)
