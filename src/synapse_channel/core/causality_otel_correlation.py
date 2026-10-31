# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — correlate model usage and policy denials into task traces
"""Place recorded model usage and claim denials into the task traces they belong to.

The coordination projection in :mod:`synapse_channel.core.causality_otel` covers
claims, handoffs, checkpoints, task updates and releases. This module adds the
two recorded facts an operator needs next to them:

* a **model call**: every ``usage`` progress note becomes a span named
  ``model usage <model>`` carrying ``gen_ai.request.model``,
  ``gen_ai.usage.input_tokens`` and ``gen_ai.usage.output_tokens`` as
  integers, plus the recorded call count and, when the recorder supplied it,
  the cost as a float;
* a **policy decision**: every claim denial becomes a span in the trace of the
  task it refused. The denial row stores only the SHA-256 of the task id, so
  it is matched against the projected task ids and never guessed.

Attribute names follow the OpenTelemetry GenAI semantic conventions pinned by
:data:`CORRELATION_SCHEMA_URL`. Prompts, completions, messages, tool arguments
and tool results are never read or emitted. Records of tasks a filter
excluded, and records that match no recorded task, are counted separately,
never dropped silently.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Collection, Sequence

from synapse_channel.core.accounting import USAGE_NOTE_KIND, parse_usage_note
from synapse_channel.core.causality_otel import (
    SPAN_STATUS_UNSET,
    OtelSpanRecord,
    SpanAttributeValue,
    span_id_for_event,
    span_id_for_root,
    trace_id_for_task,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent

CORRELATION_SCHEMA_URL = "https://opentelemetry.io/schemas/1.44.0"
"""OpenTelemetry schema the correlation attribute names belong to."""

CORRELATION_KINDS = frozenset({EventKind.LEDGER_PROGRESS, EventKind.CLAIM_DENIAL})
"""Event kinds read to correlate model usage and policy denials."""

GENAI_REQUEST_MODEL = "gen_ai.request.model"
GENAI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GENAI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"


def correlation_spans(
    events: Sequence[StoredEvent],
    task_ids: Collection[str],
    service_name: str,
    *,
    recorded_task_ids: Collection[str] | None = None,
) -> tuple[dict[str, tuple[OtelSpanRecord, ...]], int, int]:
    """Build model-usage and claim-denial spans for the projected tasks.

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
    tuple[dict[str, tuple[OtelSpanRecord, ...]], int, int]
        Spans per projected task id in sequence order, the number of usage or
        denial records that match no recorded task, and the number that belong
        to a recorded task outside ``task_ids``.
    """
    projected = frozenset(task_ids)
    recorded = projected | frozenset(recorded_task_ids or ())
    by_digest = {hashlib.sha256(task.encode("utf-8")).hexdigest(): task for task in recorded}
    spans: dict[str, list[OtelSpanRecord]] = defaultdict(list)
    uncorrelated = filtered_out = 0
    for event in sorted(events, key=lambda item: item.seq):
        if event.kind == EventKind.LEDGER_PROGRESS:
            record = _usage_span(event, service_name)
        elif event.kind == EventKind.CLAIM_DENIAL:
            record = _denial_span(event, by_digest, service_name)
        else:
            continue
        if record is None:
            continue
        task_id, span = record
        if task_id in projected:
            spans[task_id].append(span)
        elif task_id in recorded:
            filtered_out += 1
        else:
            uncorrelated += 1
    return {task: tuple(items) for task, items in spans.items()}, uncorrelated, filtered_out


def _usage_span(event: StoredEvent, service_name: str) -> tuple[str, OtelSpanRecord] | None:
    """Return the task id and usage span of one usage note, or ``None`` otherwise."""
    payload = event.payload
    if str(payload.get("kind", "")) != USAGE_NOTE_KIND:
        return None
    parsed = parse_usage_note(str(payload.get("text", "")))
    if parsed is None:
        return None
    task_id = str(payload.get("task_id", ""))
    cost = parsed.get("cost")
    attributes = _attributes(
        (GENAI_REQUEST_MODEL, str(parsed["model"])),
        (GENAI_USAGE_INPUT_TOKENS, int(parsed["input_tokens"])),
        (GENAI_USAGE_OUTPUT_TOKENS, int(parsed["output_tokens"])),
        ("synapse.usage.calls", int(parsed["calls"])),
        ("synapse.usage.cost_usd", None if cost is None else float(cost)),
        ("synapse.agent", str(payload.get("author", ""))),
        ("synapse.kind", event.kind),
        ("synapse.seq", str(event.seq)),
        ("synapse.task_id", task_id),
        ("service.name", service_name),
    )
    return task_id, _point_span(task_id, event, f"model usage {parsed['model']}", attributes)


def _denial_span(
    event: StoredEvent, by_digest: dict[str, str], service_name: str
) -> tuple[str, OtelSpanRecord]:
    """Return the matched task id (empty when none) and policy span of one denial."""
    payload = event.payload
    task_id = by_digest.get(str(payload.get("task_id_sha256", "")), "")
    attributes = _attributes(
        ("synapse.policy.decision", "denied"),
        ("synapse.policy.reason", str(payload.get("reason_code", ""))),
        ("synapse.claimant", str(payload.get("claimant", ""))),
        ("synapse.kind", event.kind),
        ("synapse.seq", str(event.seq)),
        ("synapse.task_id", task_id),
        ("service.name", service_name),
    )
    return task_id, _point_span(task_id, event, f"claim denied {task_id}".strip(), attributes)


def _point_span(
    task_id: str,
    event: StoredEvent,
    name: str,
    attributes: tuple[tuple[str, SpanAttributeValue], ...],
) -> OtelSpanRecord:
    """Return one point span under the task root."""
    timestamp = int(event.ts * 1_000_000_000)
    return OtelSpanRecord(
        trace_id_hex=trace_id_for_task(task_id),
        span_id_hex=span_id_for_event(event.seq),
        parent_span_id_hex=span_id_for_root(task_id),
        name=name,
        start_ns=timestamp,
        end_ns=timestamp,
        attributes=attributes,
        links=(),
        status=SPAN_STATUS_UNSET,
    )


def _attributes(
    *pairs: tuple[str, SpanAttributeValue | None],
) -> tuple[tuple[str, SpanAttributeValue], ...]:
    """Return the attribute pairs sorted by key, without absent or empty values.

    A count of ``0`` is a recorded value and is kept.
    """
    present = [(key, value) for key, value in pairs if value is not None and value != ""]
    return tuple(sorted(present, key=lambda pair: pair[0]))
