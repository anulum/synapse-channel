# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — correlation contract v1 spans in single-hub task traces
"""Place model usage, policy denials and operation records into their task traces.

The coordination projection in :mod:`synapse_channel.core.causality_otel` covers
claims, handoffs, checkpoints, task updates and releases. This module adds the
facts of correlation contract v1 (:mod:`synapse_channel.core.correlation_contract`)
next to them, so one task trace answers: which model calls ran, which policy
decision refused a claim, which operation was declared, what its outcome was,
what a verifier observed, and what the recorded calls cost.

* a **model call**: a ``usage`` progress note becomes ``model usage <model>``
  with ``gen_ai.request.model``, ``gen_ai.usage.input_tokens`` and
  ``gen_ai.usage.output_tokens`` as integers, the recorded call count and,
  when the recorder supplied it, the cost as a float;
* a **policy decision**: a claim denial becomes ``claim denied <task>`` in the
  trace of the task it refused. The denial row stores only the SHA-256 of the
  task id, so it is matched against the projected task ids and never guessed;
* an **operation**: a declaration becomes ``operation <operation_id>``, from
  the declaration to its last outcome or verification row, and those rows
  become its children ``operation outcome <state>`` and
  ``operation verified <effect> <result>``.

The facts are read by :func:`synapse_channel.core.correlation_points.correlation_points`;
this module gives them single-hub span ids. Prompts, completions, messages,
tool arguments and results, operation targets, observations and evidence
digests are never read or emitted.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from synapse_channel.core.causality_otel import (
    SPAN_STATUS_UNSET,
    OtelSpanRecord,
    span_id_for_event,
    span_id_for_root,
    trace_id_for_task,
)
from synapse_channel.core.correlation_contract import (
    CORRELATION_CONTRACT,
    CORRELATION_SCHEMA_URL,
    GENAI_REQUEST_MODEL,
    GENAI_USAGE_INPUT_TOKENS,
    GENAI_USAGE_OUTPUT_TOKENS,
)
from synapse_channel.core.correlation_points import (
    CORRELATION_KINDS,
    CorrelationPoint,
    correlation_points,
)
from synapse_channel.core.persistence import StoredEvent

__all__ = [
    "CORRELATION_CONTRACT",
    "CORRELATION_KINDS",
    "CORRELATION_SCHEMA_URL",
    "GENAI_REQUEST_MODEL",
    "GENAI_USAGE_INPUT_TOKENS",
    "GENAI_USAGE_OUTPUT_TOKENS",
    "CorrelatedSpans",
    "correlation_spans",
    "point_span",
]


@dataclass(frozen=True)
class CorrelatedSpans:
    """Correlation spans per projected task, with what was counted instead.

    Attributes
    ----------
    by_task : Mapping[str, tuple[OtelSpanRecord, ...]]
        Spans per projected task in sequence order.
    uncorrelated : int
        Records that match no task the log records.
    filtered_out : int
        Records of recorded tasks outside the projection.
    unreadable : int
        Usage notes and operation rows that could not be read.
    """

    by_task: Mapping[str, tuple[OtelSpanRecord, ...]]
    uncorrelated: int
    filtered_out: int
    unreadable: int


def correlation_spans(
    events: Sequence[StoredEvent],
    task_ids: Collection[str],
    service_name: str,
    *,
    recorded_task_ids: Collection[str] | None = None,
) -> CorrelatedSpans:
    """Build the contract v1 spans of the projected tasks.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Loaded events in any order; kinds outside :data:`CORRELATION_KINDS` and
        progress notes that are not usage notes are ignored.
    task_ids : Collection[str]
        Task ids whose traces the projection contains.
    service_name : str
        The ``service.name`` value stamped on every span.
    recorded_task_ids : Collection[str] or None, optional
        Every task the log records with coordination events, including tasks a
        filter excluded; defaults to ``task_ids``.

    Returns
    -------
    CorrelatedSpans
        Spans per projected task and the three counters.
    """
    points = correlation_points(events, task_ids, recorded_task_ids=recorded_task_ids)
    return CorrelatedSpans(
        by_task={
            task: tuple(point_span(point, service_name) for point in items)
            for task, items in points.by_task.items()
        },
        uncorrelated=points.uncorrelated,
        filtered_out=points.filtered_out,
        unreadable=points.unreadable,
    )


def point_span(point: CorrelationPoint, service_name: str) -> OtelSpanRecord:
    """Return one point as a span under its task root or its operation span.

    Parameters
    ----------
    point : CorrelationPoint
        The fact to project.
    service_name : str
        The ``service.name`` value stamped on the span.

    Returns
    -------
    OtelSpanRecord
        The span; its id is the event id of the point's row.
    """
    parent = (
        span_id_for_root(point.task_id)
        if point.parent_seq is None
        else span_id_for_event(point.parent_seq)
    )
    attributes = sorted(
        (*point.attributes, ("service.name", service_name)), key=lambda pair: pair[0]
    )
    return OtelSpanRecord(
        trace_id_hex=trace_id_for_task(point.task_id),
        span_id_hex=span_id_for_event(point.seq),
        parent_span_id_hex=parent,
        name=point.name,
        start_ns=_nanoseconds(point.start_ts),
        end_ns=_nanoseconds(point.end_ts),
        attributes=tuple(attributes),
        links=(),
        status=SPAN_STATUS_UNSET,
    )


def _nanoseconds(timestamp: float) -> int:
    """Return a journal timestamp in integer nanoseconds."""
    return int(timestamp * 1_000_000_000)
