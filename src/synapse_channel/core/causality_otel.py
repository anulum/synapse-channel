# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — project the coordination-causality graph onto OpenTelemetry span records
"""Project the coordination-causality graph onto OpenTelemetry span records.

:mod:`synapse_channel.core.causality` folds the durable event log into a graph of
recorded coordination relations. Observability backends speak OpenTelemetry, so
this module projects that graph onto the OTel trace model: **one trace per
task** (a deterministic trace id derived from the task id), a root span covering
the task's recorded lifetime, one child span per coordination event, and — the
part that carries the causality — a **span link** on every event that a recorded
``dependency`` or ``contention`` edge enabled, pointing at the causing event's
span in the other task's trace. "This claim proceeded because that release freed
its paths" becomes a first-class link any trace viewer renders.

The projection is **pure and deterministic**: ids are SHA-256 derivations of the
task id and event sequence, so re-exporting the same log yields byte-identical
records and cross-task links always resolve. This module imports nothing from
OpenTelemetry — it emits plain span *records* (frozen dataclasses / JSON), which
:mod:`synapse_channel.otel_export` converts to SDK spans for a real OTLP push
behind the optional ``[otel]`` extra. Same honest scope as the causality module:
spans and links reflect *recorded* coordination events and relations, timestamps
are the hub's own event timestamps, and the fold is read-only over the log —
lifecycle ordering is conveyed by the per-task trace itself, so only the
cross-task relations (``dependency``, ``contention``) become links.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.causality import (
    DEFAULT_MAX_GRAPH_NODES,
    GRAPH_KINDS,
    LIFECYCLE,
    CausalGraph,
    CausalNode,
    build_causal_graph,
)
from synapse_channel.core.persistence import EventStore, StoredEvent

TRACE_ID_HEX_LENGTH = 32
"""OTel trace ids are 16 bytes — 32 lowercase hex characters."""

SPAN_ID_HEX_LENGTH = 16
"""OTel span ids are 8 bytes — 16 lowercase hex characters."""

_TRACE_DOMAIN = b"synapse-channel:otel:trace:"
_ROOT_DOMAIN = b"synapse-channel:otel:root:"
_EVENT_DOMAIN = b"synapse-channel:otel:event:"

SERVICE_NAME = "synapse-channel"
"""The OTel ``service.name`` resource attribute stamped on exported spans."""


@dataclass(frozen=True)
class SpanLinkRecord:
    """A causal link from one event span to the span of the event that enabled it.

    Attributes
    ----------
    trace_id_hex : str
        Trace id of the causing event's task, 32 hex characters.
    span_id_hex : str
        Span id of the causing event, 16 hex characters.
    relation : str
        The recorded relation the link derives from (``dependency`` or
        ``contention``).
    detail : str
        The edge's recorded explanation.
    """

    trace_id_hex: str
    span_id_hex: str
    relation: str
    detail: str


@dataclass(frozen=True)
class OtelSpanRecord:
    """One span of the causality projection, independent of any OTel SDK.

    Attributes
    ----------
    trace_id_hex : str
        Trace id (32 hex characters) — deterministic per task.
    span_id_hex : str
        Span id (16 hex characters) — deterministic per event (or task root).
    parent_span_id_hex : str
        The task root span's id for an event span; empty for a root span.
    name : str
        Span name — the task id for a root, ``kind task`` for an event.
    start_ns : int
        Start time in nanoseconds since the epoch (the event timestamp).
    end_ns : int
        End time in nanoseconds; equals ``start_ns`` for point events.
    attributes : tuple[tuple[str, str], ...]
        Sorted string attribute pairs (empty values omitted).
    links : tuple[SpanLinkRecord, ...]
        Causal links to the spans of enabling events in other tasks.
    """

    trace_id_hex: str
    span_id_hex: str
    parent_span_id_hex: str
    name: str
    start_ns: int
    end_ns: int
    attributes: tuple[tuple[str, str], ...]
    links: tuple[SpanLinkRecord, ...]


@dataclass(frozen=True)
class OtelProjection:
    """The full span projection of one event log.

    Attributes
    ----------
    spans : tuple[OtelSpanRecord, ...]
        Root spans and event spans, roots first per task, tasks in first-seen
        (sequence) order.
    trace_count : int
        Number of task traces projected.
    skipped_events : int
        Coordination events that carried no task id — they belong to no trace
        and are counted rather than silently dropped.
    """

    spans: tuple[OtelSpanRecord, ...]
    trace_count: int
    skipped_events: int


def trace_id_for_task(task_id: str) -> str:
    """Return the deterministic 32-hex-character trace id of a task."""
    return hashlib.sha256(_TRACE_DOMAIN + task_id.encode("utf-8")).hexdigest()[:TRACE_ID_HEX_LENGTH]


def span_id_for_event(seq: int) -> str:
    """Return the deterministic 16-hex-character span id of an event sequence."""
    digest = hashlib.sha256(_EVENT_DOMAIN + str(seq).encode("utf-8"))
    return digest.hexdigest()[:SPAN_ID_HEX_LENGTH]


def span_id_for_root(task_id: str) -> str:
    """Return the deterministic 16-hex-character span id of a task's root span."""
    digest = hashlib.sha256(_ROOT_DOMAIN + task_id.encode("utf-8"))
    return digest.hexdigest()[:SPAN_ID_HEX_LENGTH]


def build_otel_projection(events: Sequence[StoredEvent]) -> OtelProjection:
    """Project an event log onto OpenTelemetry span records.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Loaded events, in any order.

    Returns
    -------
    OtelProjection
        One trace per task: a root span covering the task's recorded lifetime,
        a child span per coordination event, and links for the cross-task
        ``dependency``/``contention`` edges.
    """
    graph = build_causal_graph(events)
    by_task: dict[str, list[CausalNode]] = defaultdict(list)
    skipped = 0
    for node in graph.nodes:
        if node.task_id:
            by_task[node.task_id].append(node)
        else:
            skipped += 1
    links = _links_by_destination(graph)
    spans: list[OtelSpanRecord] = []
    for task_id, nodes in by_task.items():
        spans.append(_root_span(task_id, nodes))
        spans.extend(_event_span(task_id, node, links.get(node.seq, ())) for node in nodes)
    return OtelProjection(spans=tuple(spans), trace_count=len(by_task), skipped_events=skipped)


def run_otel_projection(
    db_path: str | Path,
    *,
    max_nodes: int | None = DEFAULT_MAX_GRAPH_NODES,
) -> OtelProjection:
    """Build the span projection from an existing SQLite event store.

    Streams only coordination events off the store cursor, exactly as
    :func:`~synapse_channel.core.causality.run_causality` does, bounded by
    ``max_nodes``.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    max_nodes : int or None, optional
        Fail-closed ceiling on coordination events; ``None`` or ``0`` lifts it.

    Returns
    -------
    OtelProjection
        The projection built from persisted events.

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
                    f"otel projection would exceed {max_nodes} coordination events; "
                    f"bound the log with `synapse compact` or raise --max-nodes"
                )
                raise ValueError(msg)
    finally:
        store.close()
    return build_otel_projection(events)


def projection_to_json(projection: OtelProjection) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a span projection."""
    return {
        "service_name": SERVICE_NAME,
        "trace_count": projection.trace_count,
        "skipped_events": projection.skipped_events,
        "spans": [_span_to_json(span) for span in projection.spans],
    }


def _links_by_destination(graph: CausalGraph) -> dict[int, tuple[SpanLinkRecord, ...]]:
    """Index the cross-task causal edges as links keyed by their effect event.

    Lifecycle edges order events *within* one task, which the per-task trace
    already conveys; only ``dependency`` and ``contention`` cross task
    boundaries and become links.
    """
    by_seq = {node.seq: node for node in graph.nodes}
    links: dict[int, list[SpanLinkRecord]] = defaultdict(list)
    for edge in graph.edges:
        if edge.relation == LIFECYCLE:
            continue
        source = by_seq[edge.src]
        if not source.task_id:
            continue
        links[edge.dst].append(
            SpanLinkRecord(
                trace_id_hex=trace_id_for_task(source.task_id),
                span_id_hex=span_id_for_event(source.seq),
                relation=edge.relation,
                detail=edge.detail,
            )
        )
    return {seq: tuple(items) for seq, items in links.items()}


def _root_span(task_id: str, nodes: Sequence[CausalNode]) -> OtelSpanRecord:
    """Build a task's root span covering its recorded lifetime."""
    attributes = _attributes(
        ("synapse.task_id", task_id),
        ("synapse.events", str(len(nodes))),
        ("synapse.final_status", nodes[-1].status),
        ("service.name", SERVICE_NAME),
    )
    return OtelSpanRecord(
        trace_id_hex=trace_id_for_task(task_id),
        span_id_hex=span_id_for_root(task_id),
        parent_span_id_hex="",
        name=task_id,
        start_ns=_nanoseconds(nodes[0].ts),
        end_ns=_nanoseconds(nodes[-1].ts),
        attributes=attributes,
        links=(),
    )


def _event_span(
    task_id: str, node: CausalNode, links: tuple[SpanLinkRecord, ...]
) -> OtelSpanRecord:
    """Build one coordination event's point span, carrying its causal links."""
    attributes = _attributes(
        ("synapse.seq", str(node.seq)),
        ("synapse.kind", node.kind),
        ("synapse.task_id", task_id),
        ("synapse.owner", node.owner),
        ("synapse.status", node.status),
        ("synapse.worktree", node.worktree),
        ("synapse.paths", ",".join(node.paths)),
        ("synapse.text", node.text),
        ("service.name", SERVICE_NAME),
    )
    timestamp = _nanoseconds(node.ts)
    return OtelSpanRecord(
        trace_id_hex=trace_id_for_task(task_id),
        span_id_hex=span_id_for_event(node.seq),
        parent_span_id_hex=span_id_for_root(task_id),
        name=f"{node.kind} {task_id}",
        start_ns=timestamp,
        end_ns=timestamp,
        attributes=attributes,
        links=links,
    )


def _attributes(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    """Return the sorted attribute pairs whose values are non-empty."""
    return tuple(sorted((key, value) for key, value in pairs if value))


def _nanoseconds(ts: float) -> int:
    """Convert an event timestamp (seconds) to integer nanoseconds."""
    return int(ts * 1_000_000_000)


def _span_to_json(span: OtelSpanRecord) -> dict[str, object]:
    """Convert one span record into JSON-compatible fields."""
    return {
        "trace_id": span.trace_id_hex,
        "span_id": span.span_id_hex,
        "parent_span_id": span.parent_span_id_hex,
        "name": span.name,
        "start_ns": span.start_ns,
        "end_ns": span.end_ns,
        "attributes": dict(span.attributes),
        "links": [
            {
                "trace_id": link.trace_id_hex,
                "span_id": link.span_id_hex,
                "relation": link.relation,
                "detail": link.detail,
            }
            for link in span.links
        ],
    }
