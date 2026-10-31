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
task id and event sequence, so re-exporting the same retained log with the same
options yields matching records. Links identify other projected events; viewer
resolution requires their export and retention in the backend. Ids do not include
a hub identity, so separate journals need separate observability context. This
module imports nothing from OpenTelemetry — it emits plain span *records*
(frozen dataclasses / JSON), which
:mod:`synapse_channel.otel_export` converts to SDK spans for a real OTLP push
behind the optional ``[otel]`` extra. Same honest scope as the causality module:
spans and links reflect *recorded* coordination events and relations, timestamps
are the hub's own event timestamps, and the fold is read-only over the log —
lifecycle ordering is conveyed by the per-task trace itself, so only the
cross-task relations (``dependency``, ``contention``) become links.

A span's status is derived from the one failure terminal the enforced task
lifecycle records (:class:`~synapse_channel.core.lifecycle.TaskStatus.FAILED`):
an event carrying it — and a task whose final recorded status is it — projects
span status ``ERROR``; everything else stays ``UNSET``, because the log records
progress, not success verdicts, and inventing ``OK`` would overstate it. The
projection can be narrowed to named tasks (``task_filter``); links from a span
to a task outside the batch are **kept**, never truncated — the ids are
deterministic, so such a link resolves against any export that carried the
other task, exactly like any partial OTel export batch.
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
from synapse_channel.core.lifecycle import TaskStatus
from synapse_channel.core.persistence import EventStore, StoredEvent

TRACE_ID_HEX_LENGTH = 32
"""OTel trace ids are 16 bytes — 32 lowercase hex characters."""

SPAN_ID_HEX_LENGTH = 16
"""OTel span ids are 8 bytes — 16 lowercase hex characters."""

_TRACE_DOMAIN = b"synapse-channel:otel:trace:"
_ROOT_DOMAIN = b"synapse-channel:otel:root:"
_EVENT_DOMAIN = b"synapse-channel:otel:event:"

SERVICE_NAME = "synapse-channel"
"""The default OTel ``service.name`` resource attribute stamped on exported spans."""

SPAN_STATUS_ERROR = "ERROR"
"""Span status of an event recording the lifecycle's failure terminal."""

SPAN_STATUS_UNSET = "UNSET"
"""Span status of every other event — the log records progress, not verdicts."""


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


SpanAttributeValue = str | int | float
"""Value type of a span attribute: coordination values are strings, while
correlated usage counts are ``int`` and cost is ``float``, as the OpenTelemetry
GenAI conventions type them."""


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
    attributes : tuple[tuple[str, SpanAttributeValue], ...]
        Attribute pairs sorted by key (empty values omitted). Coordination
        values are strings; correlated token and call counts are ``int`` and
        the recorded cost is ``float``.
    links : tuple[SpanLinkRecord, ...]
        Causal links to the spans of enabling events in other tasks.
    status : str
        ``ERROR`` when the event (or, for a root, the task's final recorded
        status) is the lifecycle failure terminal; ``UNSET`` otherwise.
    """

    trace_id_hex: str
    span_id_hex: str
    parent_span_id_hex: str
    name: str
    start_ns: int
    end_ns: int
    attributes: tuple[tuple[str, SpanAttributeValue], ...]
    links: tuple[SpanLinkRecord, ...]
    status: str = SPAN_STATUS_UNSET


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
    service_name : str
        The ``service.name`` resource value the spans carry.
    filtered_out_tasks : int
        Tasks present in the log but excluded by a ``task_filter``; ``0`` when
        no filter narrowed the projection.
    schema_url : str
        OpenTelemetry schema of the attribute names; set only when model usage
        and policy denials were correlated, otherwise empty.
    uncorrelated_events : int
        Usage notes and claim denials that match no task the log records with
        coordination events; ``0`` when correlation was not requested.
    filtered_out_events : int
        Usage notes and claim denials of tasks a ``task_filter`` excluded;
        ``0`` without a filter or without correlation.
    """

    spans: tuple[OtelSpanRecord, ...]
    trace_count: int
    skipped_events: int
    service_name: str = SERVICE_NAME
    filtered_out_tasks: int = 0
    schema_url: str = ""
    uncorrelated_events: int = 0
    filtered_out_events: int = 0


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


def build_otel_projection(
    events: Sequence[StoredEvent],
    *,
    service_name: str = SERVICE_NAME,
    task_filter: Sequence[str] | None = None,
    correlate: bool = False,
) -> OtelProjection:
    """Project an event log onto OpenTelemetry span records.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Loaded events, in any order.
    service_name : str, optional
        The ``service.name`` resource value to stamp on the spans; defaults to
        :data:`SERVICE_NAME`. Several hubs exporting into one observability
        tenant distinguish themselves here.
    task_filter : Sequence[str] or None, optional
        Project only the named tasks' traces. A named task absent from the log
        is refused with :class:`ValueError` rather than silently exporting
        nothing for it. Links pointing at excluded tasks are kept — the ids
        are deterministic, so they resolve against any export that carried the
        other task — and the excluded tasks are counted, never silently
        truncated.
    correlate : bool, optional
        Also place recorded model usage and claim denials into their task
        traces (see :mod:`synapse_channel.core.causality_otel_correlation`)
        and stamp the projection with that schema. Defaults to ``False``.

    Returns
    -------
    OtelProjection
        One trace per task: a root span covering the task's recorded lifetime,
        a child span per coordination event, links for the cross-task
        ``dependency``/``contention`` edges and, when correlating, one span per
        usage note or claim denial of that task.

    Raises
    ------
    ValueError
        If ``task_filter`` names a task the log does not record.
    """
    graph = build_causal_graph(events)
    by_task: dict[str, list[CausalNode]] = defaultdict(list)
    skipped = 0
    for node in graph.nodes:
        if node.task_id:
            by_task[node.task_id].append(node)
        else:
            skipped += 1
    recorded_tasks = frozenset(by_task)
    filtered_out = 0
    if task_filter is not None:
        wanted = set(task_filter)
        missing = sorted(wanted - by_task.keys())
        if missing:
            msg = f"task(s) not recorded in the log: {', '.join(missing)}"
            raise ValueError(msg)
        filtered_out = len(by_task) - len(wanted)
        by_task = {task_id: nodes for task_id, nodes in by_task.items() if task_id in wanted}
    links = _links_by_destination(graph)
    correlated: dict[str, tuple[OtelSpanRecord, ...]] = {}
    uncorrelated = filtered_events = 0
    schema_url = ""
    if correlate:
        from synapse_channel.core.causality_otel_correlation import (
            CORRELATION_SCHEMA_URL,
            correlation_spans,
        )

        correlated, uncorrelated, filtered_events = correlation_spans(
            events, frozenset(by_task), service_name, recorded_task_ids=recorded_tasks
        )
        schema_url = CORRELATION_SCHEMA_URL
    spans: list[OtelSpanRecord] = []
    for task_id, nodes in by_task.items():
        spans.append(_root_span(task_id, nodes, service_name))
        spans.extend(
            _event_span(task_id, node, links.get(node.seq, ()), service_name) for node in nodes
        )
        spans.extend(correlated.get(task_id, ()))
    return OtelProjection(
        spans=tuple(spans),
        trace_count=len(by_task),
        skipped_events=skipped,
        service_name=service_name,
        filtered_out_tasks=filtered_out,
        schema_url=schema_url,
        uncorrelated_events=uncorrelated,
        filtered_out_events=filtered_events,
    )


def run_otel_projection(
    db_path: str | Path,
    *,
    max_nodes: int | None = DEFAULT_MAX_GRAPH_NODES,
    service_name: str = SERVICE_NAME,
    task_filter: Sequence[str] | None = None,
    key_file: str | Path | None = None,
    correlate: bool = False,
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
    service_name : str, optional
        The ``service.name`` resource value to stamp on the spans.
    task_filter : Sequence[str] or None, optional
        Project only the named tasks' traces; a task the log does not record
        is refused. See :func:`build_otel_projection`.
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key for an encrypted event store.
    correlate : bool, optional
        Also read usage notes and claim denials and place them into their task
        traces. They count toward ``max_nodes`` like coordination events.

    Returns
    -------
    OtelProjection
        The projection built from persisted events.

    Raises
    ------
    ValueError
        If the event store does not exist, the log holds more coordination
        events than ``max_nodes``, or ``task_filter`` names an unrecorded
        task.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    kinds = set(GRAPH_KINDS)
    if correlate:
        from synapse_channel.core.causality_otel_correlation import CORRELATION_KINDS

        kinds |= CORRELATION_KINDS
    store = EventStore(path, key_file=key_file)
    try:
        events: list[StoredEvent] = []
        for event in store.iter_events(kinds=frozenset(kinds)):
            events.append(event)
            if max_nodes and len(events) > max_nodes:
                msg = (
                    f"otel projection would exceed {max_nodes} coordination events; "
                    f"bound the log with `synapse compact` or raise --max-nodes"
                )
                raise ValueError(msg)
    finally:
        store.close()
    return build_otel_projection(
        events, service_name=service_name, task_filter=task_filter, correlate=correlate
    )


def projection_to_json(projection: OtelProjection) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a span projection.

    The schema URL and the uncorrelated and filtered-out record counts appear
    only for a correlated projection, so an uncorrelated document keeps its
    previous shape.
    """
    document: dict[str, object] = {
        "service_name": projection.service_name,
        "trace_count": projection.trace_count,
        "skipped_events": projection.skipped_events,
        "filtered_out_tasks": projection.filtered_out_tasks,
        "spans": [_span_to_json(span) for span in projection.spans],
    }
    if projection.schema_url:
        document["schema_url"] = projection.schema_url
        document["uncorrelated_events"] = projection.uncorrelated_events
        document["filtered_out_events"] = projection.filtered_out_events
    return document


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


def _root_span(task_id: str, nodes: Sequence[CausalNode], service_name: str) -> OtelSpanRecord:
    """Build a task's root span covering its recorded lifetime."""
    attributes = _attributes(
        ("synapse.task_id", task_id),
        ("synapse.events", str(len(nodes))),
        ("synapse.final_status", nodes[-1].status),
        ("service.name", service_name),
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
        status=_span_status(nodes[-1].status),
    )


def _event_span(
    task_id: str, node: CausalNode, links: tuple[SpanLinkRecord, ...], service_name: str
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
        ("service.name", service_name),
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
        status=_span_status(node.status),
    )


def _span_status(recorded: str) -> str:
    """Map a recorded status onto span status — ``ERROR`` only for the failure terminal.

    The enforced lifecycle records exactly one failure state
    (:attr:`~synapse_channel.core.lifecycle.TaskStatus.FAILED`); anything else —
    progress, completion, or a status the lifecycle does not know — stays
    ``UNSET``, because the log records progress, not success verdicts.
    """
    return SPAN_STATUS_ERROR if recorded == TaskStatus.FAILED else SPAN_STATUS_UNSET


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
        "status": span.status,
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
