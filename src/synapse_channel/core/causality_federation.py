# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-hub coordination causality over merged multi-hub event logs
"""Trace coordination causality across federated hubs' merged event logs.

:mod:`synapse_channel.core.causality` reconstructs the coordination-causality
graph of ONE hub's durable log. Federated deployments coordinate across several
hubs — a dependency declared on one hub can be completed on another, and a
release on the namespace-owning hub can free an overlapping claim that arrived
through a peer. This module answers the same three queries over the
deterministic union of several hubs' logs
(:func:`synapse_channel.core.multihub_merge.merge_event_logs`): each event keeps
its global identity ``(hub_id, seq)``, the union is replayed in the merged total
order ``(ts, hub_id, seq)``, and the single-hub relation derivations run over
that order unchanged. An edge whose endpoints were authored by two different
hubs is tagged :data:`FEDERATION`, with the recorded relation it derives from
preserved as its ``basis``.

Honest scope, stated plainly: within one hub, precedence is the hub's own
monotonic sequence — authoritative. *Across* hubs there is no shared sequence;
the merged order falls back to event timestamps, i.e. the wall clocks of
different machines. A federation edge is therefore **clock-ordered evidence**,
only as trustworthy as the hubs' clock agreement — weaker than a single-hub
edge, and never an authority claim. This mirrors the observed-not-granted
posture of the multi-hub read side (:mod:`synapse_channel.core.multihub_fold`):
the fold observes peers' logs and grants nothing. The module is read-only, pure
over loaded events, and contacts no live hub.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.causality import (
    DEFAULT_MAX_GRAPH_NODES,
    DIRECTIONS,
    GRAPH_KINDS,
    CausalEdge,
    CausalGraph,
    CausalNode,
    CausalQuery,
    build_causal_graph,
    causes,
    counterfactual,
    effects,
)
from synapse_channel.core.multihub_merge import merge_event_logs, tag_events
from synapse_channel.core.persistence import EventStore, StoredEvent

FEDERATION = "federation"
"""Relation tag: a recorded relation whose endpoints two different hubs authored."""


@dataclass(frozen=True)
class HubEventRef:
    """The global identity of one event in a federated log: ``(hub_id, seq)``.

    Attributes
    ----------
    hub_id : str
        Id of the hub that authored the event.
    seq : int
        The authoring hub's local monotonic sequence number.
    """

    hub_id: str
    seq: int

    def render(self) -> str:
        """Return the ``hub:seq`` form used on the CLI and in Markdown."""
        return f"{self.hub_id}:{self.seq}"


@dataclass(frozen=True)
class FederatedNode:
    """One coordination event placed in the federated causality graph.

    The same projection as :class:`synapse_channel.core.causality.CausalNode`,
    with the authoring hub added and ``seq`` meaning that hub's *local* sequence.

    Attributes
    ----------
    hub_id : str
        Id of the hub that authored the event.
    seq : int
        The authoring hub's local event-log sequence.
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

    hub_id: str
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

    @property
    def ref(self) -> HubEventRef:
        """Return the node's global identity."""
        return HubEventRef(hub_id=self.hub_id, seq=self.seq)


@dataclass(frozen=True)
class FederatedEdge:
    """A directed causal edge between events, possibly authored by different hubs.

    Attributes
    ----------
    src : HubEventRef
        Global identity of the cause event.
    dst : HubEventRef
        Global identity of the effect event.
    relation : str
        :data:`FEDERATION` when ``src`` and ``dst`` were authored by different
        hubs, otherwise the recorded single-hub relation.
    basis : str
        The recorded relation the edge derives from
        (:data:`~synapse_channel.core.causality.LIFECYCLE`,
        :data:`~synapse_channel.core.causality.DEPENDENCY`, or
        :data:`~synapse_channel.core.causality.CONTENTION`) — equal to
        ``relation`` for a same-hub edge.
    detail : str
        Short explanation of why the edge exists.
    """

    src: HubEventRef
    dst: HubEventRef
    relation: str
    basis: str
    detail: str


@dataclass(frozen=True)
class FederatedCausalGraph:
    """The coordination-causality graph over a merged multi-hub log.

    Attributes
    ----------
    nodes : tuple[FederatedNode, ...]
        Graph nodes in the merged total order ``(ts, hub_id, seq)``.
    edges : tuple[FederatedEdge, ...]
        Causal edges, in the merged order of ``(src, dst)``.
    hubs : tuple[str, ...]
        The hub ids whose logs were merged, sorted.
    """

    nodes: tuple[FederatedNode, ...]
    edges: tuple[FederatedEdge, ...]
    hubs: tuple[str, ...]


@dataclass(frozen=True)
class FederatedLink:
    """A one-hop federated edge paired with the node at its far end."""

    edge: FederatedEdge
    node: FederatedNode


@dataclass(frozen=True)
class FederatedQuery:
    """The answer to one causality query against a federated event reference.

    Attributes
    ----------
    ref : HubEventRef
        The queried event's global identity.
    direction : str
        One of :data:`~synapse_channel.core.causality.DIRECTIONS`.
    present : bool
        Whether ``ref`` names a coordination event in the merged graph.
    node : FederatedNode or None
        The queried node, or ``None`` when absent.
    direct : tuple[FederatedLink, ...]
        Immediate one-hop neighbours (causes upstream, effects downstream).
    transitive : tuple[FederatedNode, ...]
        The full ancestry or descendant closure, excluding the queried node,
        in merged order.
    unsupported : tuple[FederatedNode, ...]
        Counterfactual-only: descendants whose every recorded cause traces
        back through ``ref``. Empty for ``causes`` and ``effects``.
    hubs : tuple[str, ...]
        The hub ids whose logs were merged, sorted.
    """

    ref: HubEventRef
    direction: str
    present: bool
    node: FederatedNode | None
    direct: tuple[FederatedLink, ...]
    transitive: tuple[FederatedNode, ...]
    unsupported: tuple[FederatedNode, ...]
    hubs: tuple[str, ...]


def parse_hub_ref(text: str, default_hub: str) -> HubEventRef:
    """Parse a CLI event reference — ``SEQ`` or ``HUB:SEQ`` — into its identity.

    Parameters
    ----------
    text : str
        The reference as typed; a plain integer names an event on
        ``default_hub``, a ``HUB:SEQ`` form names the hub explicitly.
    default_hub : str
        Hub id a plain-integer reference resolves against.

    Returns
    -------
    HubEventRef
        The parsed global identity.

    Raises
    ------
    ValueError
        If the sequence part is not an integer or the hub part is empty.
    """
    hub, sep, seq_text = text.rpartition(":")
    if sep and not hub:
        msg = f"invalid event reference '{text}': the hub part before ':' is empty"
        raise ValueError(msg)
    try:
        seq = int(seq_text)
    except ValueError:
        msg = f"invalid event reference '{text}': expected SEQ or HUB:SEQ with an integer SEQ"
        raise ValueError(msg) from None
    return HubEventRef(hub_id=hub if sep else default_hub, seq=seq)


def build_federated_graph(logs: Mapping[str, Sequence[StoredEvent]]) -> FederatedCausalGraph:
    """Fold several hubs' event logs into one federated causality graph.

    Parameters
    ----------
    logs : Mapping[str, Sequence[StoredEvent]]
        Each hub's loaded events, keyed by its hub id.

    Returns
    -------
    FederatedCausalGraph
        Nodes for every coordination event across all logs, and the recorded
        relations derived over the merged total order — cross-hub edges tagged
        :data:`FEDERATION`.
    """
    inner, refs = _synthesise(logs)
    return FederatedCausalGraph(
        nodes=tuple(_federated_node(refs[node.seq], node) for node in inner.nodes),
        edges=tuple(_federated_edge(edge, refs) for edge in inner.edges),
        hubs=tuple(sorted(logs)),
    )


def federated_query(
    logs: Mapping[str, Sequence[StoredEvent]],
    direction: str,
    ref: HubEventRef,
) -> FederatedQuery:
    """Answer one causality query against a federated event reference.

    Parameters
    ----------
    logs : Mapping[str, Sequence[StoredEvent]]
        Each hub's loaded events, keyed by its hub id.
    direction : str
        One of :data:`~synapse_channel.core.causality.DIRECTIONS`.
    ref : HubEventRef
        The queried event's global identity.

    Returns
    -------
    FederatedQuery
        The query answer over the merged graph.

    Raises
    ------
    ValueError
        If ``direction`` is unsupported or ``ref`` names a hub outside ``logs``.
    """
    if direction not in DIRECTIONS:
        msg = f"unknown direction '{direction}'; expected one of {', '.join(DIRECTIONS)}"
        raise ValueError(msg)
    if ref.hub_id not in logs:
        known = ", ".join(sorted(logs))
        msg = f"unknown hub '{ref.hub_id}' in event reference; merged hubs: {known}"
        raise ValueError(msg)
    inner, refs = _synthesise(logs)
    index = {node_ref: seq for seq, node_ref in refs.items()}.get((ref.hub_id, ref.seq), 0)
    query_fn = {"causes": causes, "effects": effects, "counterfactual": counterfactual}[direction]
    answer = query_fn(inner, index)
    return _federated_answer(answer, ref, refs, tuple(sorted(logs)))


def run_federated_causality(
    db_paths: Mapping[str, str | Path],
    direction: str,
    ref: HubEventRef,
    *,
    max_nodes: int | None = DEFAULT_MAX_GRAPH_NODES,
) -> FederatedQuery:
    """Build a federated causality query from several SQLite event stores.

    Only coordination events (:data:`~synapse_channel.core.causality.GRAPH_KINDS`)
    are read from each store, streaming off the cursor as
    :func:`~synapse_channel.core.causality.run_causality` does; ``max_nodes``
    bounds the coordination events of the whole union.

    Parameters
    ----------
    db_paths : Mapping[str, str or pathlib.Path]
        Each hub's event-store database path, keyed by its hub id.
    direction : str
        One of :data:`~synapse_channel.core.causality.DIRECTIONS`.
    ref : HubEventRef
        The queried event's global identity.
    max_nodes : int or None, optional
        Fail-closed ceiling on coordination events folded across all logs;
        ``None`` or ``0`` lifts it. Defaults to
        :data:`~synapse_channel.core.causality.DEFAULT_MAX_GRAPH_NODES`.

    Returns
    -------
    FederatedQuery
        The query answer built from the persisted logs.

    Raises
    ------
    ValueError
        If a store is missing, ``direction`` is unsupported, ``ref`` names an
        unknown hub, or the union holds more coordination events than
        ``max_nodes``.
    """
    logs: dict[str, Sequence[StoredEvent]] = {}
    total = 0
    for hub_id, db_path in db_paths.items():
        path = Path(db_path)
        if not path.exists():
            msg = f"missing event store for hub '{hub_id}': {path}"
            raise ValueError(msg)
        store = EventStore(path)
        try:
            events: list[StoredEvent] = []
            for event in store.iter_events(kinds=GRAPH_KINDS):
                events.append(event)
                total += 1
                if max_nodes and total > max_nodes:
                    msg = (
                        f"federated causality graph would exceed {max_nodes} coordination "
                        f"events across {len(db_paths)} hubs; bound the logs with "
                        f"`synapse compact` or raise --max-nodes"
                    )
                    raise ValueError(msg)
        finally:
            store.close()
        logs[hub_id] = events
    return federated_query(logs, direction, ref)


def federated_to_json(query: FederatedQuery) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a federated query."""
    return {
        "ref": _ref_to_json(query.ref),
        "direction": query.direction,
        "present": query.present,
        "hubs": list(query.hubs),
        "node": _node_to_json(query.node) if query.node is not None else None,
        "direct": [_link_to_json(link) for link in query.direct],
        "transitive": [_node_to_json(node) for node in query.transitive],
        "unsupported": [_node_to_json(node) for node in query.unsupported],
    }


def render_federated_markdown(query: FederatedQuery) -> str:
    """Render a federated causality query as compact Markdown."""
    heading = f"# Federated causality ({query.direction}): {query.ref.render()}"
    if query.node is None:
        return (
            f"{heading}\n\nNo coordination event at {query.ref.render()} "
            f"across hubs {', '.join(query.hubs)}."
        )
    label = "causes" if query.direction == "causes" else "effects"
    lines = [
        heading,
        "",
        f"- Hubs: {', '.join(query.hubs)}",
        f"- Event: {_node_summary(query.node)}",
        f"- Direct {label}: {len(query.direct)}",
        f"- Transitive: {len(query.transitive)}",
    ]
    if query.direction == "counterfactual":
        lines.append(f"- Loses recorded support: {len(query.unsupported)}")
    lines.append("")
    lines.append(f"## Direct {label}")
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


def _synthesise(
    logs: Mapping[str, Sequence[StoredEvent]],
) -> tuple[CausalGraph, dict[int, tuple[str, int]]]:
    """Merge the logs and derive the causal graph over a dense synthetic order.

    The merged total order ``(ts, hub_id, seq)`` is re-sequenced ``1..n`` so the
    single-hub derivations — which read ``seq`` only for order and identity —
    run over the merged order unchanged. Returns the derived graph plus the map
    from each synthetic sequence back to the event's global ``(hub_id, seq)``.
    """
    tagged = [
        event for hub_id, events in sorted(logs.items()) for event in tag_events(hub_id, events)
    ]
    merged = merge_event_logs(tagged)
    refs: dict[int, tuple[str, int]] = {}
    synthetic: list[StoredEvent] = []
    for index, event in enumerate(merged, start=1):
        refs[index] = (event.hub_id, event.seq)
        synthetic.append(
            StoredEvent(seq=index, ts=event.ts, kind=event.kind, payload=dict(event.payload))
        )
    return build_causal_graph(synthetic), refs


def _federated_node(ref: tuple[str, int], node: CausalNode) -> FederatedNode:
    """Restore a synthetic-graph node's global identity."""
    hub_id, seq = ref
    return FederatedNode(
        hub_id=hub_id,
        seq=seq,
        ts=node.ts,
        kind=node.kind,
        task_id=node.task_id,
        owner=node.owner,
        status=node.status,
        paths=node.paths,
        worktree=node.worktree,
        depends_on=node.depends_on,
        text=node.text,
    )


def _federated_edge(edge: CausalEdge, refs: dict[int, tuple[str, int]]) -> FederatedEdge:
    """Translate a synthetic-graph edge, tagging cross-hub edges :data:`FEDERATION`."""
    src_hub, src_seq = refs[edge.src]
    dst_hub, dst_seq = refs[edge.dst]
    return FederatedEdge(
        src=HubEventRef(hub_id=src_hub, seq=src_seq),
        dst=HubEventRef(hub_id=dst_hub, seq=dst_seq),
        relation=FEDERATION if src_hub != dst_hub else edge.relation,
        basis=edge.relation,
        detail=edge.detail,
    )


def _federated_answer(
    answer: CausalQuery,
    ref: HubEventRef,
    refs: dict[int, tuple[str, int]],
    hubs: tuple[str, ...],
) -> FederatedQuery:
    """Translate a synthetic-graph query answer back to global identities."""
    return FederatedQuery(
        ref=ref,
        direction=answer.direction,
        present=answer.present,
        node=_federated_node(refs[answer.node.seq], answer.node) if answer.node else None,
        direct=tuple(
            FederatedLink(
                edge=_federated_edge(link.edge, refs),
                node=_federated_node(refs[link.node.seq], link.node),
            )
            for link in answer.direct
        ),
        transitive=tuple(_federated_node(refs[node.seq], node) for node in answer.transitive),
        unsupported=tuple(_federated_node(refs[node.seq], node) for node in answer.unsupported),
        hubs=hubs,
    )


def _node_summary(node: FederatedNode) -> str:
    """Return a compact one-line summary of a federated node."""
    owner = f" owner={node.owner}" if node.owner else ""
    status = f" status={node.status}" if node.status else ""
    task = f" task={node.task_id}" if node.task_id else ""
    return f"{node.ref.render()} kind={node.kind}{task}{owner}{status}"


def _render_link(link: FederatedLink) -> str:
    """Render one direct federated link, exposing a cross-hub edge's basis."""
    edge = link.edge
    tag = f"{FEDERATION}:{edge.basis}" if edge.relation == FEDERATION else edge.relation
    return f"- [{tag}] {link.node.ref.render()} kind={link.node.kind} — {edge.detail}"


def _ref_to_json(ref: HubEventRef) -> dict[str, object]:
    """Convert a global identity into JSON-compatible fields."""
    return {"hub_id": ref.hub_id, "seq": ref.seq}


def _node_to_json(node: FederatedNode) -> dict[str, object]:
    """Convert a federated node into JSON-compatible fields."""
    return {
        "hub_id": node.hub_id,
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


def _link_to_json(link: FederatedLink) -> dict[str, object]:
    """Convert a direct federated link into JSON-compatible fields."""
    return {
        "relation": link.edge.relation,
        "basis": link.edge.basis,
        "src": _ref_to_json(link.edge.src),
        "dst": _ref_to_json(link.edge.dst),
        "detail": link.edge.detail,
        "node": _node_to_json(link.node),
    }
