# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality graph over the durable event log
"""Reconstruct a coordination-causality graph from the durable hub event log.

The hub records *coordination* events — claims, releases, task updates, handoffs,
and ledger declarations — and those events carry the precedence the hub's own
scheduling implies: a task is claimed before it is updated or released, a declared
dependency must complete before its dependent starts, and overlapping claims on
the same paths are serialised by release. This module folds the log into a
directed acyclic graph of those three recorded relations and answers three
queries against an event sequence:

* ``causes`` — the events that had to happen before the queried event;
* ``effects`` — the events the queried event enabled downstream;
* ``counterfactual`` — the downstream events whose recorded support would vanish
  if the queried event were removed from the log.

This is *coordination* causality, inferred from recorded scheduling semantics —
not statistical causal discovery (no Granger tests, no do-calculus) and not
program-trace causality. Every edge is backed by a concrete event in the log.
The counterfactual is a structural what-if over the inferred graph: it shows
which events lose their recorded cause, not a claim that an agent would never
have done the work by another route. The module is read-only and contacts no
live hub, mirroring :mod:`synapse_channel.core.postmortem` and
:mod:`synapse_channel.core.replay`.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.replay import SNAPSHOT_KINDS

LIFECYCLE = "lifecycle"
"""Relation tag: consecutive events of one task in sequence order."""

DEPENDENCY = "dependency"
"""Relation tag: a declared ``depends_on`` satisfied by the dependency's completion."""

CONTENTION = "contention"
"""Relation tag: a release that let a later, path-overlapping claim proceed."""

RELATIONS = (LIFECYCLE, DEPENDENCY, CONTENTION)
"""All causal-edge relation tags, in render order."""

GRAPH_KINDS = SNAPSHOT_KINDS | {EventKind.RELEASE, EventKind.LEDGER_TASK}
"""Event kinds that become nodes in the coordination-causality graph."""

DONE_STATUSES = frozenset({"done", "released", "complete", "completed", "resolved", "closed"})
"""Status markers that count a task's snapshot as a completion for dependency edges."""

DIRECTIONS = ("causes", "effects", "counterfactual")
"""The supported query directions."""


@dataclass(frozen=True)
class CausalNode:
    """One coordination event placed in the causality graph.

    Attributes
    ----------
    seq : int
        Durable event-log sequence; the node's identity in the graph.
    ts : float
        Event timestamp.
    kind : str
        Durable event kind.
    task_id : str
        Task the event concerns.
    owner : str
        Claim owner carried by the event, or empty.
    status : str
        Task/claim status carried by the event, or empty.
    paths : tuple[str, ...]
        Declared path scopes carried by a claim snapshot.
    worktree : str
        Worktree the claim is scoped to, or empty.
    depends_on : tuple[str, ...]
        Declared dependency task ids carried by a ledger declaration.
    text : str
        Best-effort human-readable summary of the event.
    """

    seq: int
    ts: float
    kind: str
    task_id: str
    owner: str
    status: str
    paths: tuple[str, ...]
    worktree: str
    depends_on: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class CausalEdge:
    """A directed causal edge from an earlier event to a later one.

    Edges always point from a lower sequence to a higher one, so the graph is
    acyclic by construction.

    Attributes
    ----------
    src : int
        Sequence of the cause event.
    dst : int
        Sequence of the effect event.
    relation : str
        One of :data:`RELATIONS`.
    detail : str
        Short explanation of why the edge exists.
    """

    src: int
    dst: int
    relation: str
    detail: str


@dataclass(frozen=True)
class CausalGraph:
    """The coordination-causality graph reconstructed from an event log.

    Attributes
    ----------
    nodes : tuple[CausalNode, ...]
        Graph nodes in ascending sequence order.
    edges : tuple[CausalEdge, ...]
        Causal edges, ordered by ``(src, dst, relation)``.
    generated_from_seq : int
        Highest event sequence considered.
    """

    nodes: tuple[CausalNode, ...]
    edges: tuple[CausalEdge, ...]
    generated_from_seq: int


@dataclass(frozen=True)
class CausalLink:
    """A one-hop edge paired with the node at its far end."""

    edge: CausalEdge
    node: CausalNode


@dataclass(frozen=True)
class CausalQuery:
    """The answer to one causality query against a sequence point.

    Attributes
    ----------
    seq : int
        The queried event sequence.
    direction : str
        One of :data:`DIRECTIONS`.
    present : bool
        Whether ``seq`` names a coordination event in the graph.
    node : CausalNode or None
        The queried node, or ``None`` when ``seq`` is not a coordination event.
    direct : tuple[CausalLink, ...]
        Immediate one-hop neighbours (causes upstream, effects downstream).
    transitive : tuple[CausalNode, ...]
        The full ancestry (``causes``) or descendant closure
        (``effects``/``counterfactual``), excluding the queried node, in
        sequence order.
    unsupported : tuple[CausalNode, ...]
        Counterfactual-only: descendants whose every recorded cause traces back
        through ``seq``, so they lose all recorded support if it is removed.
        Empty for ``causes`` and ``effects``.
    """

    seq: int
    direction: str
    present: bool
    node: CausalNode | None
    direct: tuple[CausalLink, ...]
    transitive: tuple[CausalNode, ...]
    unsupported: tuple[CausalNode, ...]


def build_causal_graph(events: Sequence[StoredEvent]) -> CausalGraph:
    """Fold an event log into a coordination-causality graph.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Loaded events, in any order.

    Returns
    -------
    CausalGraph
        Nodes for every coordination event and lifecycle, dependency, and
        contention edges between them.
    """
    ordered = sorted(
        (event for event in events if event.kind in GRAPH_KINDS),
        key=lambda item: item.seq,
    )
    nodes = tuple(_node_from_event(event) for event in ordered)
    by_task: dict[str, list[CausalNode]] = defaultdict(list)
    for node in nodes:
        if node.task_id:
            by_task[node.task_id].append(node)
    edges = (
        *_lifecycle_edges(by_task),
        *_dependency_edges(by_task),
        *_contention_edges(nodes),
    )
    ordered_edges = tuple(sorted(edges, key=lambda edge: (edge.src, edge.dst, edge.relation)))
    generated = max((node.seq for node in nodes), default=0)
    return CausalGraph(nodes=nodes, edges=ordered_edges, generated_from_seq=generated)


def causes(graph: CausalGraph, seq: int) -> CausalQuery:
    """Return the events that had to happen before ``seq``."""
    return _query(graph, seq, "causes")


def effects(graph: CausalGraph, seq: int) -> CausalQuery:
    """Return the events ``seq`` enabled downstream."""
    return _query(graph, seq, "effects")


def counterfactual(graph: CausalGraph, seq: int) -> CausalQuery:
    """Return the downstream events that lose recorded support without ``seq``."""
    return _query(graph, seq, "counterfactual")


DEFAULT_MAX_GRAPH_NODES = 250_000
"""Fail-closed ceiling on coordination events folded into one causality graph.

Generous for any real deployment (a quarter-million claims, releases, and task
updates), yet a hard bound against the pathological log that would otherwise
exhaust memory. Raised or lifted per call when an operator genuinely needs more.
"""


def run_causality(
    db_path: str | Path,
    direction: str,
    seq: int,
    *,
    max_nodes: int | None = DEFAULT_MAX_GRAPH_NODES,
) -> CausalQuery:
    """Build a causality query from an existing SQLite event store.

    Only coordination events (:data:`GRAPH_KINDS`) are read: the kind filter runs
    inside SQLite and rows stream off the cursor, so the bulk of a long-lived
    log — chat — never reaches Python, and the peak footprint is the coordination
    nodes alone, bounded by ``max_nodes``.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    direction : str
        One of :data:`DIRECTIONS`.
    seq : int
        Event sequence to query.
    max_nodes : int or None, optional
        Fail-closed ceiling on coordination events folded into the graph;
        exceeding it raises instead of exhausting memory. ``None`` or ``0``
        lifts the ceiling. Defaults to :data:`DEFAULT_MAX_GRAPH_NODES`.

    Returns
    -------
    CausalQuery
        The query answer built from persisted events.

    Raises
    ------
    ValueError
        If the event store does not exist, ``direction`` is unsupported, or the
        log holds more coordination events than ``max_nodes``.
    """
    if direction not in DIRECTIONS:
        msg = f"unknown direction '{direction}'; expected one of {', '.join(DIRECTIONS)}"
        raise ValueError(msg)
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
    graph = build_causal_graph(events)
    return _query(graph, seq, direction)


def causality_to_json(query: CausalQuery) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a causality query."""
    return {
        "seq": query.seq,
        "direction": query.direction,
        "present": query.present,
        "node": _node_to_json(query.node) if query.node is not None else None,
        "direct": [_link_to_json(link) for link in query.direct],
        "transitive": [_node_to_json(node) for node in query.transitive],
        "unsupported": [_node_to_json(node) for node in query.unsupported],
    }


def render_markdown(query: CausalQuery) -> str:
    """Render a causality query as compact Markdown."""
    node = query.node
    if node is None:
        return (
            f"# Causality ({query.direction}): seq {query.seq}\n\n"
            f"No coordination event at seq {query.seq}."
        )
    lines = [
        f"# Causality ({query.direction}): seq {query.seq}",
        "",
        f"- Event: {_node_summary(node)}",
        f"- Direct {_direct_label(query.direction)}: {len(query.direct)}",
        f"- Transitive: {len(query.transitive)}",
    ]
    if query.direction == "counterfactual":
        lines.append(f"- Loses recorded support: {len(query.unsupported)}")
    lines.append("")
    lines.append(f"## Direct {_direct_label(query.direction)}")
    if query.direct:
        lines.extend(_render_link(link) for link in query.direct)
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Transitive")
    if query.transitive:
        lines.extend(f"- {_node_summary(item)}" for item in query.transitive)
    else:
        lines.append("- none")
    if query.direction == "counterfactual":
        lines.append("")
        lines.append("## Loses recorded support")
        if query.unsupported:
            lines.extend(f"- {_node_summary(item)}" for item in query.unsupported)
        else:
            lines.append("- none")
    return "\n".join(lines)


def _query(graph: CausalGraph, seq: int, direction: str) -> CausalQuery:
    """Answer one causality query against a sequence point."""
    by_seq = {node.seq: node for node in graph.nodes}
    node = by_seq.get(seq)
    if node is None:
        return CausalQuery(
            seq=seq,
            direction=direction,
            present=False,
            node=None,
            direct=(),
            transitive=(),
            unsupported=(),
        )
    forward, backward = _adjacency(graph.edges)
    if direction == "causes":
        adjacency, reach_from = backward, backward
        endpoint = "src"
    else:
        adjacency, reach_from = forward, forward
        endpoint = "dst"
    direct = _direct_links(adjacency.get(seq, ()), by_seq, endpoint)
    reached = sorted(_reach(reach_from, seq, endpoint) - {seq})
    transitive = tuple(by_seq[node_seq] for node_seq in reached)
    unsupported: tuple[CausalNode, ...] = ()
    if direction == "counterfactual":
        unsupported = _unsupported(seq, set(reached), backward, by_seq)
    return CausalQuery(
        seq=seq,
        direction=direction,
        present=True,
        node=node,
        direct=direct,
        transitive=transitive,
        unsupported=unsupported,
    )


def _adjacency(
    edges: Sequence[CausalEdge],
) -> tuple[dict[int, list[CausalEdge]], dict[int, list[CausalEdge]]]:
    """Return forward (keyed by ``src``) and backward (keyed by ``dst``) adjacency."""
    forward: dict[int, list[CausalEdge]] = defaultdict(list)
    backward: dict[int, list[CausalEdge]] = defaultdict(list)
    for edge in edges:
        forward[edge.src].append(edge)
        backward[edge.dst].append(edge)
    return forward, backward


def _direct_links(
    edges: Iterable[CausalEdge],
    by_seq: dict[int, CausalNode],
    endpoint: str,
) -> tuple[CausalLink, ...]:
    """Pair each edge with the node at its far end (``src`` or ``dst``)."""
    links = [CausalLink(edge=edge, node=by_seq[getattr(edge, endpoint)]) for edge in edges]
    links.sort(key=lambda link: (link.node.seq, link.edge.relation))
    return tuple(links)


def _reach(adjacency: dict[int, list[CausalEdge]], start: int, endpoint: str) -> set[int]:
    """Return all sequences reachable from ``start`` over ``adjacency``."""
    seen = {start}
    queue: deque[int] = deque([start])
    while queue:
        current = queue.popleft()
        for edge in adjacency.get(current, ()):
            nxt = getattr(edge, endpoint)
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _unsupported(
    seq: int,
    affected: set[int],
    backward: dict[int, list[CausalEdge]],
    by_seq: dict[int, CausalNode],
) -> tuple[CausalNode, ...]:
    """Return descendants whose every recorded cause traces back through ``seq``.

    A descendant is unsupported when each of its incoming edges originates at
    ``seq`` or at another unsupported descendant. Computed as a fixpoint so a node
    kept alive only by an unsupported neighbour also collapses. A descendant with
    any incoming edge from a node outside ``affected`` keeps independent support.
    Every descendant was reached over a forward edge, so it always has at least one
    incoming edge.
    """
    unsupported: set[int] = set()
    changed = True
    while changed:
        changed = False
        for node_seq in affected:
            if node_seq in unsupported:
                continue
            sources = [edge.src for edge in backward.get(node_seq, ())]
            if all(src == seq or src in unsupported for src in sources):
                unsupported.add(node_seq)
                changed = True
    return tuple(by_seq[node_seq] for node_seq in sorted(unsupported))


def _lifecycle_edges(by_task: dict[str, list[CausalNode]]) -> list[CausalEdge]:
    """Return edges between consecutive events of each task."""
    edges: list[CausalEdge] = []
    for nodes in by_task.values():
        ordered = sorted(nodes, key=lambda node: node.seq)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            edges.append(
                CausalEdge(
                    src=earlier.seq,
                    dst=later.seq,
                    relation=LIFECYCLE,
                    detail=f"{earlier.kind} → {later.kind}",
                )
            )
    return edges


def _dependency_edges(by_task: dict[str, list[CausalNode]]) -> list[CausalEdge]:
    """Return edges from a dependency's completion to its dependent's start."""
    edges: list[CausalEdge] = []
    for task_id, nodes in by_task.items():
        ordered = sorted(nodes, key=lambda node: node.seq)
        anchor = _first_claim(ordered)
        if anchor is None:
            continue
        for dependency in _declared_dependencies(ordered):
            enabling = _completion_before(by_task.get(dependency, ()), anchor.seq)
            if enabling is not None:
                edges.append(
                    CausalEdge(
                        src=enabling.seq,
                        dst=anchor.seq,
                        relation=DEPENDENCY,
                        detail=f"task {task_id} depends on {dependency}",
                    )
                )
    return edges


def _first_claim(nodes: Sequence[CausalNode]) -> CausalNode | None:
    """Return a task's earliest actual claim (snapshot) node, or ``None``.

    A dependency gates when work *starts* — the first claim — not when the task is
    merely declared in the ledger. A task that was declared but never claimed has
    no realised dependency edge.
    """
    for node in nodes:
        if node.kind in SNAPSHOT_KINDS:
            return node
    return None


def _declared_dependencies(nodes: Sequence[CausalNode]) -> tuple[str, ...]:
    """Return the dependencies declared by a task's latest ledger node."""
    for node in reversed(nodes):
        if node.kind == EventKind.LEDGER_TASK and node.depends_on:
            return node.depends_on
    return ()


def _completion_before(nodes: Iterable[CausalNode], before_seq: int) -> CausalNode | None:
    """Return the latest completion of a task strictly before ``before_seq``."""
    completions = [
        node
        for node in nodes
        if node.seq < before_seq
        and (node.kind == EventKind.RELEASE or node.status in DONE_STATUSES)
    ]
    if not completions:
        return None
    return max(completions, key=lambda node: node.seq)


def _contention_edges(nodes: Sequence[CausalNode]) -> list[CausalEdge]:
    """Return edges from a release to a later, path-overlapping fresh claim.

    Walks events in order, tracking live claims and the claims that were just
    released. When a *fresh* claim (a task not currently held) acquires paths that
    overlap a recently released claim by another owner in the same worktree, the
    release is recorded as the cause that let the claim proceed.
    """
    edges: list[CausalEdge] = []
    live: dict[str, CausalNode] = {}
    released: list[tuple[int, CausalNode]] = []
    for node in nodes:
        if node.kind == EventKind.RELEASE:
            held = live.pop(node.task_id, None)
            if held is not None:
                released.append((node.seq, held))
            continue
        if node.kind not in SNAPSHOT_KINDS:
            continue
        if node.task_id not in live:
            blocker = _latest_overlapping_release(node, released)
            if blocker is not None:
                edges.append(
                    CausalEdge(
                        src=blocker,
                        dst=node.seq,
                        relation=CONTENTION,
                        detail=f"claim on {node.task_id} freed by an overlapping release",
                    )
                )
        live[node.task_id] = node
    return edges


def _latest_overlapping_release(
    claim: CausalNode,
    released: Sequence[tuple[int, CausalNode]],
) -> int | None:
    """Return the latest release sequence whose claim gated ``claim``.

    ``released`` is built in event order, so its release sequences ascend; the last
    overlapping entry is therefore the latest, and a running assignment suffices.
    """
    latest: int | None = None
    for release_seq, freed in released:
        if release_seq >= claim.seq or freed.owner == claim.owner:
            continue
        if freed.worktree != claim.worktree:
            continue
        if not paths_overlap(freed.paths, claim.paths):
            continue
        latest = release_seq
    return latest


def paths_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    """Return whether two path-scope sets overlap (an empty scope means the whole tree).

    The same predicate the contention edges use; the yield-advice module weighs
    overlapping live claims with it, so both surfaces agree on what "overlap" means.
    """
    if not left or not right:
        return True
    return any(
        _path_pair_overlaps(left_path, right_path) for left_path in left for right_path in right
    )


def _path_pair_overlaps(left: str, right: str) -> bool:
    """Return whether two repository-relative paths overlap."""
    left_clean = left.rstrip("/")
    right_clean = right.rstrip("/")
    return (
        left_clean == right_clean
        or left_clean.startswith(f"{right_clean}/")
        or right_clean.startswith(f"{left_clean}/")
    )


def _node_from_event(event: StoredEvent) -> CausalNode:
    """Project a stored event into a causality-graph node."""
    payload = event.payload
    return CausalNode(
        seq=event.seq,
        ts=event.ts,
        kind=event.kind,
        task_id=str(payload.get("task_id", "")),
        owner=str(payload.get("owner", "")),
        status=str(payload.get("status", "")),
        paths=tuple(str(path) for path in payload.get("paths", ())),
        worktree=str(payload.get("worktree", "")),
        depends_on=tuple(str(dep) for dep in payload.get("depends_on", ())),
        text=_event_text(event),
    )


def _event_text(event: StoredEvent) -> str:
    """Return the most useful text field carried by an event."""
    payload = event.payload
    for key in ("title", "note", "text", "data_ref"):
        value = str(payload.get(key, ""))
        if value:
            return value
    return ""


def _node_summary(node: CausalNode) -> str:
    """Return a compact one-line summary of a node."""
    owner = f" owner={node.owner}" if node.owner else ""
    status = f" status={node.status}" if node.status else ""
    task = f" task={node.task_id}" if node.task_id else ""
    return f"seq={node.seq} kind={node.kind}{task}{owner}{status}"


def _direct_label(direction: str) -> str:
    """Return the noun for a direction's direct neighbours."""
    return "causes" if direction == "causes" else "effects"


def _render_link(link: CausalLink) -> str:
    """Render one direct causal link."""
    return (
        f"- [{link.edge.relation}] seq={link.node.seq} kind={link.node.kind} — {link.edge.detail}"
    )


def _node_to_json(node: CausalNode) -> dict[str, object]:
    """Convert a node into JSON-compatible fields."""
    return {
        "seq": node.seq,
        "ts": node.ts,
        "kind": node.kind,
        "task_id": node.task_id,
        "owner": node.owner,
        "status": node.status,
        "paths": list(node.paths),
        "worktree": node.worktree,
        "depends_on": list(node.depends_on),
        "text": node.text,
    }


def _link_to_json(link: CausalLink) -> dict[str, object]:
    """Convert a direct link into JSON-compatible fields."""
    return {
        "relation": link.edge.relation,
        "src": link.edge.src,
        "dst": link.edge.dst,
        "detail": link.edge.detail,
        "node": _node_to_json(link.node),
    }
