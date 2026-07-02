# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — queryable evidence graph over durable coordination events
"""Queryable agent trust graph built from the durable hub event log.

The graph realises the `agent trust graph design <agent-trust-graph.md>`_ as a
read-only projection: typed evidence edges between agent and task nodes, each
carrying the event-log provenance (sequence, timestamp, detail, evidence
fields) that created it. It composes the two evidence layers that already
exist — evidence-only reliability memory
(:mod:`~synapse_channel.core.reliability`) and observed capability evidence
from positive release receipts
(:mod:`~synapse_channel.core.capability_observations`) — so the graph never
invents a fact the log does not hold.

It is deliberately not a score, rank, or reputation system: nodes carry no
grade, edges carry evidence with provenance, and negative evidence (a stale
claim, a declared failed check, a broken handoff, a conflict pair) stays a
reviewable fact rather than a penalty. Filtering by agent, task, or a
``since`` timestamp gives the operator the decay and focus controls the design
calls for without hiding judgement inside a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from synapse_channel.core.capability_observations import build_observed_capability_index
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.reliability import ReliabilityFinding, build_reliability_report

TRUST_GRAPH_BOUNDARY = (
    "evidence with event-log provenance, not scores; authorship is as recorded "
    "in the local log and is not cryptographically verified here"
)
"""Boundary note stamped on every report, mirroring the reliability wording."""

AGENT_NODE = "agent"
TASK_NODE = "task"

POSITIVE_RECEIPT_EDGE = "positive_receipt"
CONFLICT_PAIR_EDGE = "conflict_pair"

UNKNOWN_LABEL = "(unknown)"
"""Label used when an event carries no author or task id."""


def _agent_id(name: str) -> str:
    return f"agent:{name or UNKNOWN_LABEL}"


def _task_id(name: str) -> str:
    return f"task:{name or UNKNOWN_LABEL}"


@dataclass(frozen=True)
class TrustGraphNode:
    """One agent or task entity referenced by at least one evidence edge."""

    id: str
    kind: str
    label: str


@dataclass(frozen=True)
class TrustGraphEdge:
    """One typed evidence edge with event-log provenance.

    Attributes
    ----------
    source, target : str
        Node ids. Evidence about an agent's own task work points agent → task;
        a conflict pair links the two agents and names both tasks in
        ``tasks`` and ``evidence``.
    kind : str
        ``positive_receipt``, ``stale_claim``, ``declared_failed_check``,
        ``broken_handoff_candidate``, or ``conflict_pair``.
    seq : int
        Durable event-log sequence that created the evidence.
    ts : float
        Event timestamp.
    detail : str
        Short human-readable description carried from the source record.
    tasks : tuple[str, ...]
        Every task id the edge involves, so task filtering also reaches
        agent-to-agent conflict edges.
    evidence : dict[str, Any]
        Stable machine-readable evidence fields from the source layer.
    """

    source: str
    target: str
    kind: str
    seq: int
    ts: float
    detail: str
    tasks: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrustGraph:
    """Evidence graph over one durable event log, with the boundary note."""

    generated_from_seq: int
    as_of: float
    nodes: tuple[TrustGraphNode, ...]
    edges: tuple[TrustGraphEdge, ...]
    trust_boundary: str = TRUST_GRAPH_BOUNDARY


def build_trust_graph(events: list[StoredEvent], *, as_of: float | None = None) -> TrustGraph:
    """Build the evidence graph from loaded events.

    Parameters
    ----------
    events : list[StoredEvent]
        Durable events read from an :class:`EventStore`.
    as_of : float or None, optional
        Timestamp used by the reliability layer to decide whether live claims
        and handoffs are stale; the latest event timestamp when omitted.

    Returns
    -------
    TrustGraph
        Typed evidence edges between agent and task nodes, deterministic
        order, no scores.
    """
    reliability = build_reliability_report(events, as_of=as_of)
    observations = build_observed_capability_index(events)

    edges: list[TrustGraphEdge] = []
    for observed in observations.evidence:
        edges.append(
            TrustGraphEdge(
                source=_agent_id(observed.agent),
                target=_task_id(observed.task_id),
                kind=POSITIVE_RECEIPT_EDGE,
                seq=observed.seq,
                ts=observed.ts,
                detail=observed.detail,
                tasks=(observed.task_id,),
                evidence={"source": observed.source, "tokens": list(observed.tokens)},
            )
        )
    seen_conflicts: set[tuple[str, str, str, str]] = set()
    for finding in reliability.findings:
        edge = _finding_edge(finding, seen_conflicts)
        if edge is not None:
            edges.append(edge)

    edges.sort(key=lambda edge: (edge.seq, edge.kind, edge.source, edge.target))
    return TrustGraph(
        generated_from_seq=reliability.generated_from_seq,
        as_of=reliability.as_of,
        nodes=_nodes_for(edges),
        edges=tuple(edges),
    )


def _finding_edge(
    finding: ReliabilityFinding,
    seen_conflicts: set[tuple[str, str, str, str]],
) -> TrustGraphEdge | None:
    """Map one reliability finding to an edge; a conflict pair maps only once.

    The reliability layer emits a conflict as two symmetric findings (one per
    owner) sharing an evidence dict; the graph keeps one agent-to-agent edge.
    """
    if finding.kind != CONFLICT_PAIR_EDGE:
        return TrustGraphEdge(
            source=_agent_id(finding.owner),
            target=_task_id(finding.task_id),
            kind=finding.kind,
            seq=finding.seq,
            ts=finding.ts,
            detail=finding.detail,
            tasks=(finding.task_id,),
            evidence=dict(finding.evidence),
        )
    left_owner = str(finding.evidence.get("left_owner", ""))
    right_owner = str(finding.evidence.get("right_owner", ""))
    left_task = str(finding.evidence.get("left_task", ""))
    right_task = str(finding.evidence.get("right_task", ""))
    key = (left_owner, left_task, right_owner, right_task)
    if key in seen_conflicts:
        return None
    seen_conflicts.add(key)
    return TrustGraphEdge(
        source=_agent_id(left_owner),
        target=_agent_id(right_owner),
        kind=CONFLICT_PAIR_EDGE,
        seq=finding.seq,
        ts=finding.ts,
        detail=finding.detail,
        tasks=(left_task, right_task),
        evidence=dict(finding.evidence),
    )


def _nodes_for(edges: list[TrustGraphEdge]) -> tuple[TrustGraphNode, ...]:
    """Return the sorted entity nodes referenced by ``edges``."""
    nodes: dict[str, TrustGraphNode] = {}
    for edge in edges:
        for node_id in (edge.source, edge.target):
            kind, _, label = node_id.partition(":")
            nodes[node_id] = TrustGraphNode(id=node_id, kind=kind, label=label)
        for task in edge.tasks:
            task_node = _task_id(task)
            nodes.setdefault(
                task_node,
                TrustGraphNode(id=task_node, kind=TASK_NODE, label=task or UNKNOWN_LABEL),
            )
    return tuple(sorted(nodes.values(), key=lambda node: (node.kind, node.label)))


def run_trust_graph(db_path: str | Path, *, as_of: float | None = None) -> TrustGraph:
    """Build the evidence graph from an existing SQLite event store.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path)
    try:
        events = list(store.read_all())
    finally:
        store.close()
    return build_trust_graph(events, as_of=as_of)


def graph_involving(
    graph: TrustGraph,
    *,
    agent: str | None = None,
    task: str | None = None,
    since: float | None = None,
) -> TrustGraph:
    """Return the subgraph of edges matching every given focus filter.

    Parameters
    ----------
    agent : str or None, optional
        Keep edges with this agent as an endpoint.
    task : str or None, optional
        Keep edges involving this task — as an endpoint or, for a conflict
        pair, as either conflicting task.
    since : float or None, optional
        Keep edges whose event timestamp is at or after this value: the
        operator's decay window, so aged-out evidence stops dominating a
        review without being deleted from the log.
    """
    agent_node = _agent_id(agent) if agent is not None else None
    edges = tuple(
        edge
        for edge in graph.edges
        if (agent_node is None or agent_node in (edge.source, edge.target))
        and (task is None or _task_id(task) in (edge.source, edge.target) or task in edge.tasks)
        and (since is None or edge.ts >= since)
    )
    return replace(graph, nodes=_nodes_for(list(edges)), edges=edges)


def trust_graph_to_json(graph: TrustGraph) -> dict[str, object]:
    """Return a stable JSON-compatible representation of the graph."""
    return {
        "generated_from_seq": graph.generated_from_seq,
        "as_of": graph.as_of,
        "trust_boundary": graph.trust_boundary,
        "nodes": [{"id": node.id, "kind": node.kind, "label": node.label} for node in graph.nodes],
        "edges": [
            {
                "source": edge.source,
                "target": edge.target,
                "kind": edge.kind,
                "seq": edge.seq,
                "ts": edge.ts,
                "detail": edge.detail,
                "tasks": list(edge.tasks),
                "evidence": edge.evidence,
            }
            for edge in graph.edges
        ],
        "note": "evidence graph, not scores",
    }


def _dot_quote(text: str) -> str:
    """Return ``text`` as a quoted DOT string literal."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_trust_graph_dot(graph: TrustGraph) -> str:
    """Render the graph as a Graphviz ``digraph``.

    Agents are ellipses, tasks are boxes; a conflict pair renders as a dashed
    undirected edge between the two agents. Edge labels carry the evidence
    kind and the event sequence, so a rendered picture still points back to
    the log.
    """
    lines = [
        "digraph trust_graph {",
        "  rankdir=LR;",
        f"  label={_dot_quote(f'trust graph: {graph.trust_boundary}')};",
        "  labelloc=b;",
    ]
    for node in graph.nodes:
        shape = "ellipse" if node.kind == AGENT_NODE else "box"
        lines.append(f"  {_dot_quote(node.id)} [label={_dot_quote(node.label)}, shape={shape}];")
    for edge in graph.edges:
        label = _dot_quote(f"{edge.kind} seq={edge.seq}")
        attributes = f"label={label}"
        if edge.kind == CONFLICT_PAIR_EDGE:
            attributes += ", dir=none, style=dashed"
        lines.append(f"  {_dot_quote(edge.source)} -> {_dot_quote(edge.target)} [{attributes}];")
    lines.append("}")
    return "\n".join(lines)


def render_trust_graph_human(graph: TrustGraph) -> str:
    """Render the graph as compact terminal text."""
    header = f"Trust graph: {graph.trust_boundary}"
    if not graph.edges:
        return f"{header}\n\nNo evidence edges found."
    lines = [
        header,
        f"generated_from_seq={graph.generated_from_seq} as_of={graph.as_of:.3f}",
        f"nodes={len(graph.nodes)} edges={len(graph.edges)}",
        "",
    ]
    for edge in graph.edges:
        _, _, source = edge.source.partition(":")
        _, _, target = edge.target.partition(":")
        lines.append(f"{source} -[{edge.kind} seq={edge.seq}]-> {target}: {edge.detail}")
    return "\n".join(lines)
