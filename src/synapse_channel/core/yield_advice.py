# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — causality-weighed yield advice for overlapping live claims
"""Advise which contender should yield when live claims overlap.

Two live claims by different owners whose path scopes overlap in the same
worktree are a collision waiting to happen: whichever agent finishes second
will merge into files the other rewrote. The hub serialises claims per task,
not per path, so this situation is legal — and the interesting question is not
*whether* someone should back off but *who*, and the coordination-causality
graph already holds the evidence to answer it. For each contender this module
counts what its task gates downstream: the distinct tasks reachable through
recorded causal edges from any of the task's events, plus the declared
dependents (transitively) that have not completed. The contender whose task
gates less is advised to yield; on an equal count the later claim yields, so
first-come precedence breaks the tie.

The advice is exactly that — advisory. Nothing here preempts a claim, contacts
a live hub, or mutates state; the module reads the same durable log as
:mod:`synapse_channel.core.causality` and renders a recommendation an operator
or an agent can act on with its reasoning attached.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.causality import (
    DEFAULT_MAX_GRAPH_NODES,
    DONE_STATUSES,
    GRAPH_KINDS,
    CausalGraph,
    CausalNode,
    build_causal_graph,
    paths_overlap,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.replay import SNAPSHOT_KINDS


@dataclass(frozen=True)
class ContenderStanding:
    """One side of an overlapping-claim pair, with its downstream weight.

    Attributes
    ----------
    task_id : str
        Task whose live claim is contending.
    owner : str
        Agent holding the claim.
    seq : int
        Sequence of the claim's latest live snapshot.
    paths : tuple[str, ...]
        The claim's declared path scope (empty means the whole tree).
    blocking_count : int
        Distinct tasks this claim's task gates downstream: causal descendants
        of any of the task's recorded events, plus pending declared
        dependents, transitively.
    blocked_tasks : tuple[str, ...]
        The gated task ids behind the count, sorted.
    """

    task_id: str
    owner: str
    seq: int
    paths: tuple[str, ...]
    blocking_count: int
    blocked_tasks: tuple[str, ...]


@dataclass(frozen=True)
class YieldAdvice:
    """An advisory recommendation for one overlapping live-claim pair.

    Attributes
    ----------
    holder : ContenderStanding
        The contender advised to keep working.
    yielder : ContenderStanding
        The contender advised to yield.
    reason : str
        One line explaining the recommendation.
    """

    holder: ContenderStanding
    yielder: ContenderStanding
    reason: str


def advise_yields(graph: CausalGraph) -> list[YieldAdvice]:
    """Weigh every overlapping live-claim pair and recommend who yields.

    Parameters
    ----------
    graph : CausalGraph
        The coordination-causality graph folded from the event log.

    Returns
    -------
    list[YieldAdvice]
        One advice per overlapping pair of live claims held by different
        owners in the same worktree, ordered by the earlier claim's sequence.
        Empty when no live claims overlap.
    """
    live = _live_claims(graph.nodes)
    dependents = _pending_dependents(graph.nodes)
    downstream = _downstream_tasks(graph)
    recommendations: list[YieldAdvice] = []
    for index, first in enumerate(live):
        for second in live[index + 1 :]:
            if first.owner == second.owner:
                continue
            if first.worktree != second.worktree:
                continue
            if not paths_overlap(first.paths, second.paths):
                continue
            recommendations.append(
                _weigh_pair(
                    _standing(first, dependents, downstream),
                    _standing(second, dependents, downstream),
                )
            )
    return recommendations


def run_yield_advice(
    db_path: str | Path,
    *,
    max_nodes: int | None = DEFAULT_MAX_GRAPH_NODES,
) -> list[YieldAdvice]:
    """Build yield advice from an existing SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    max_nodes : int or None, optional
        Fail-closed ceiling on coordination events folded into the graph,
        exactly as :func:`~synapse_channel.core.causality.run_causality`
        applies it. ``None`` or ``0`` lifts the ceiling.

    Returns
    -------
    list[YieldAdvice]
        Advice for every overlapping live-claim pair in the log.

    Raises
    ------
    ValueError
        If the event store does not exist or the log holds more coordination
        events than ``max_nodes``.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path)
    try:
        events: list[StoredEvent] = []
        for event in store.iter_events(kinds=GRAPH_KINDS):
            events.append(event)
            if max_nodes and len(events) > max_nodes:
                msg = (
                    f"causality graph would exceed {max_nodes} coordination events; "
                    f"bound the log with `synapse compact` or raise --max-nodes"
                )
                raise ValueError(msg)
    finally:
        store.close()
    return advise_yields(build_causal_graph(events))


def advice_to_json(recommendations: list[YieldAdvice]) -> list[dict[str, object]]:
    """Return a stable JSON-compatible representation of yield advice."""
    return [
        {
            "holder": _standing_to_json(advice.holder),
            "yielder": _standing_to_json(advice.yielder),
            "reason": advice.reason,
        }
        for advice in recommendations
    ]


def render_advice_markdown(recommendations: list[YieldAdvice]) -> str:
    """Render yield advice as compact Markdown."""
    lines = [f"# Contention: {len(recommendations)} overlapping live claim pair(s)"]
    if not recommendations:
        lines.append("")
        lines.append("No live claims overlap; nothing to weigh.")
        return "\n".join(lines)
    for advice in recommendations:
        lines.append("")
        lines.append(
            f"## {advice.yielder.task_id} ({advice.yielder.owner}) should yield to "
            f"{advice.holder.task_id} ({advice.holder.owner})"
        )
        lines.append(f"- reason: {advice.reason}")
        for label, standing in (("keeps", advice.holder), ("yields", advice.yielder)):
            blocked = ", ".join(standing.blocked_tasks) or "none"
            lines.append(
                f"- {label}: {standing.task_id} ({standing.owner}, seq {standing.seq}) "
                f"blocks {standing.blocking_count} downstream task(s): {blocked}"
            )
        lines.append("- advisory only: no claim is preempted; coordinate the yield explicitly")
    return "\n".join(lines)


def _live_claims(nodes: tuple[CausalNode, ...]) -> list[CausalNode]:
    """Return the latest live owned claim snapshot per task, in claim order.

    Mirrors the walk the contention edges use: an owned snapshot re-arms the
    task's live claim, a release or a completion status retires it. An
    ownerless snapshot changes nothing — it is no evidence the claim moved.
    """
    live: dict[str, CausalNode] = {}
    for node in nodes:
        if node.kind == EventKind.RELEASE:
            live.pop(node.task_id, None)
            continue
        if node.kind not in SNAPSHOT_KINDS:
            continue
        if node.status in DONE_STATUSES:
            live.pop(node.task_id, None)
            continue
        if node.owner:
            live[node.task_id] = node
    return sorted(live.values(), key=lambda node: node.seq)


def _pending_dependents(nodes: tuple[CausalNode, ...]) -> dict[str, set[str]]:
    """Map each task to the pending tasks that declared a dependency on it.

    A dependent counts while its latest recorded status is not a completion —
    a finished dependent no longer waits on anything.
    """
    declared: dict[str, tuple[str, ...]] = {}
    latest_status: dict[str, str] = {}
    for node in nodes:
        if node.depends_on:
            declared[node.task_id] = node.depends_on
        if node.task_id:
            latest_status[node.task_id] = node.status
    dependents: dict[str, set[str]] = defaultdict(set)
    for dependent, dependencies in declared.items():
        if latest_status.get(dependent, "") in DONE_STATUSES:
            continue
        for dependency in dependencies:
            dependents[dependency].add(dependent)
    return dependents


def _downstream_tasks(graph: CausalGraph) -> dict[str, set[str]]:
    """Map each task to the other tasks causally downstream of any of its events.

    The reach is per task, not per event: a task's earlier releases and
    updates gated real downstream work even when its *current* claim node has
    no outgoing edge yet, and that history is exactly the evidence of how much
    traffic the task gates.
    """
    by_seq = {node.seq: node.task_id for node in graph.nodes}
    forward: dict[int, list[int]] = defaultdict(list)
    for edge in graph.edges:
        forward[edge.src].append(edge.dst)
    seqs_by_task: dict[str, list[int]] = defaultdict(list)
    for node in graph.nodes:
        if node.task_id:
            seqs_by_task[node.task_id].append(node.seq)
    downstream: dict[str, set[str]] = {}
    for task_id, seqs in seqs_by_task.items():
        reached: set[str] = set()
        seen = set(seqs)
        queue = deque(seq for start in seqs for seq in forward.get(start, ()))
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            reached_task = by_seq.get(current, "")
            if reached_task and reached_task != task_id:
                reached.add(reached_task)
            queue.extend(forward.get(current, ()))
        downstream[task_id] = reached
    return downstream


def _standing(
    claim: CausalNode,
    dependents: dict[str, set[str]],
    downstream: dict[str, set[str]],
) -> ContenderStanding:
    """Fold one live claim into its downstream-blocking standing."""
    blocked = set(downstream.get(claim.task_id, ()))
    queue = deque(dependents.get(claim.task_id, ()))
    seen = {claim.task_id}
    while queue:
        task_id = queue.popleft()
        if task_id in seen:
            continue
        seen.add(task_id)
        blocked.add(task_id)
        queue.extend(dependents.get(task_id, ()))
    return ContenderStanding(
        task_id=claim.task_id,
        owner=claim.owner,
        seq=claim.seq,
        paths=claim.paths,
        blocking_count=len(blocked),
        blocked_tasks=tuple(sorted(blocked)),
    )


def _weigh_pair(first: ContenderStanding, second: ContenderStanding) -> YieldAdvice:
    """Recommend the yielder for one overlapping pair, with its reason."""
    if first.blocking_count != second.blocking_count:
        holder, yielder = (
            (first, second) if first.blocking_count > second.blocking_count else (second, first)
        )
        reason = (
            f"{holder.task_id} blocks {holder.blocking_count} downstream task(s) versus "
            f"{yielder.blocking_count}; the lighter claim yields"
        )
        return YieldAdvice(holder=holder, yielder=yielder, reason=reason)
    holder, yielder = (first, second) if first.seq < second.seq else (second, first)
    reason = (
        f"both claims block {holder.blocking_count} downstream task(s); "
        f"the later claim (seq {yielder.seq}) yields to the earlier (seq {holder.seq})"
    )
    return YieldAdvice(holder=holder, yielder=yielder, reason=reason)


def _standing_to_json(standing: ContenderStanding) -> dict[str, object]:
    """Return a stable JSON-compatible representation of one standing."""
    return {
        "task_id": standing.task_id,
        "owner": standing.owner,
        "seq": standing.seq,
        "paths": list(standing.paths),
        "blocking_count": standing.blocking_count,
        "blocked_tasks": list(standing.blocked_tasks),
    }
