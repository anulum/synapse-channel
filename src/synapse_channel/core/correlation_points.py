# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read correlation contract v1 facts out of a hub log
"""Read the facts of correlation contract v1 from journal rows, without span ids.

Each usage note, claim denial and operation record becomes one
:class:`CorrelationPoint`: its task, family, span name, start and end time,
the sequence of its parent operation declaration (or ``None`` for the task
root) and the attributes :mod:`synapse_channel.core.correlation_contract`
allows. Points carry no trace or span ids and no ``service.name``, so a
single-hub projection and a federated one can derive their own identities
from the same facts.

Rules:

* An operation point spans its declaration and every outcome and
  verification row of the same operation on the same task; its end is the
  latest of those timestamps, so it never ends before it starts.
* An outcome or verification row hangs from its declaration when that is in
  the events read, otherwise from the task root.
* A usage note that cannot be parsed and an operation row that lacks a field
  the hub always writes are counted as unreadable and emit nothing.
* Records of tasks a filter excluded and records of tasks the log does not
  record are counted apart. Nothing is dropped silently.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from synapse_channel.core.accounting import USAGE_NOTE_KIND, parse_usage_note
from synapse_channel.core.correlation_contract import (
    FAMILY_MODEL_CALL,
    FAMILY_OPERATION,
    FAMILY_OUTCOME,
    FAMILY_POLICY_DECISION,
    FAMILY_VERIFICATION,
    GENAI_REQUEST_MODEL,
    GENAI_USAGE_INPUT_TOKENS,
    GENAI_USAGE_OUTPUT_TOKENS,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent

PointAttributeValue = str | int | float
"""A point attribute value: text, an integer or a float."""

CORRELATION_KINDS = frozenset(
    {
        EventKind.LEDGER_PROGRESS,
        EventKind.CLAIM_DENIAL,
        EventKind.OPERATION_DECLARED,
        EventKind.OPERATION_OUTCOME,
        EventKind.OPERATION_VERIFIED,
    }
)
"""Event kinds read for correlation contract v1."""

_OPERATION_KINDS = frozenset(
    {EventKind.OPERATION_DECLARED, EventKind.OPERATION_OUTCOME, EventKind.OPERATION_VERIFIED}
)


@dataclass(frozen=True)
class CorrelationPoint:
    """One correlated fact of a task, before it becomes a span.

    Attributes
    ----------
    task_id : str
        The task whose trace the point joins.
    seq : int
        Journal sequence of the row.
    family : str
        One of the contract's five families.
    name : str
        The span name.
    start_ts : float
        Start time, seconds since the epoch.
    end_ts : float
        End time, never before ``start_ts``.
    parent_seq : int or None
        Sequence of the operation declaration this point hangs from, or
        ``None`` for the task root.
    attributes : tuple[tuple[str, PointAttributeValue], ...]
        Attributes sorted by key, without ``service.name``.
    """

    task_id: str
    seq: int
    family: str
    name: str
    start_ts: float
    end_ts: float
    parent_seq: int | None
    attributes: tuple[tuple[str, PointAttributeValue], ...]


@dataclass(frozen=True)
class CorrelationPoints:
    """Every point of the projected tasks, with what was counted instead.

    Attributes
    ----------
    by_task : Mapping[str, tuple[CorrelationPoint, ...]]
        Points per projected task in sequence order.
    uncorrelated : int
        Records that match no task the log records.
    filtered_out : int
        Records of recorded tasks outside the projection.
    unreadable : int
        Usage notes and operation rows that could not be read.
    """

    by_task: Mapping[str, tuple[CorrelationPoint, ...]]
    uncorrelated: int
    filtered_out: int
    unreadable: int


@dataclass
class _Read:
    """One readable row before parents and the operation end are known."""

    event: StoredEvent
    task_id: str
    family: str
    name: str
    attributes: list[tuple[str, PointAttributeValue | None]]
    operation_id: str = ""


def correlation_points(
    events: Sequence[StoredEvent],
    task_ids: Collection[str],
    *,
    recorded_task_ids: Collection[str] | None = None,
) -> CorrelationPoints:
    """Read the contract v1 points of the projected tasks.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Loaded events in any order; kinds outside :data:`CORRELATION_KINDS`
        and progress notes that are not usage notes are ignored.
    task_ids : Collection[str]
        Task ids whose traces the projection contains.
    recorded_task_ids : Collection[str] or None, optional
        Every task the log records with coordination events, including tasks a
        filter excluded; defaults to ``task_ids``.

    Returns
    -------
    CorrelationPoints
        The points per projected task and the three counters.
    """
    projected = frozenset(task_ids)
    recorded = projected | frozenset(recorded_task_ids or ())
    by_digest = {hashlib.sha256(task.encode("utf-8")).hexdigest(): task for task in recorded}
    reads: list[_Read] = []
    unreadable = 0
    for event in sorted(events, key=lambda item: item.seq):
        read = _read(event, by_digest)
        if isinstance(read, _Read):
            reads.append(read)
        elif read is False:
            unreadable += 1
    points: dict[str, list[CorrelationPoint]] = defaultdict(list)
    uncorrelated = filtered_out = 0
    for point in _resolve(reads):
        if point.task_id in projected:
            points[point.task_id].append(point)
        elif point.task_id in recorded:
            filtered_out += 1
        else:
            uncorrelated += 1
    return CorrelationPoints(
        by_task={task: tuple(items) for task, items in points.items()},
        uncorrelated=uncorrelated,
        filtered_out=filtered_out,
        unreadable=unreadable,
    )


def _read(event: StoredEvent, by_digest: Mapping[str, str]) -> _Read | Literal[False] | None:
    """Return the row's reading, ``False`` when unreadable, ``None`` when not read."""
    if event.kind == EventKind.LEDGER_PROGRESS:
        return _usage(event)
    if event.kind == EventKind.CLAIM_DENIAL:
        return _denial(event, by_digest)
    if event.kind in _OPERATION_KINDS:
        return _operation(event)
    return None


def _usage(event: StoredEvent) -> _Read | Literal[False] | None:
    """Read one usage note; other progress notes are not correlation records."""
    payload = event.payload
    if str(payload.get("kind", "")) != USAGE_NOTE_KIND:
        return None
    parsed = parse_usage_note(str(payload.get("text", "")))
    if parsed is None:
        return False
    cost = parsed.get("cost")
    return _Read(
        event,
        str(payload.get("task_id", "")),
        FAMILY_MODEL_CALL,
        f"model usage {parsed['model']}",
        [
            (GENAI_REQUEST_MODEL, str(parsed["model"])),
            (GENAI_USAGE_INPUT_TOKENS, int(parsed["input_tokens"])),
            (GENAI_USAGE_OUTPUT_TOKENS, int(parsed["output_tokens"])),
            ("synapse.usage.calls", int(parsed["calls"])),
            ("synapse.usage.cost_usd", None if cost is None else float(cost)),
            ("synapse.agent", str(payload.get("author", ""))),
        ],
    )


def _denial(event: StoredEvent, by_digest: Mapping[str, str]) -> _Read:
    """Read one claim denial; an unmatched digest leaves the task id empty."""
    payload = event.payload
    task_id = by_digest.get(str(payload.get("task_id_sha256", "")), "")
    return _Read(
        event,
        task_id,
        FAMILY_POLICY_DECISION,
        f"claim denied {task_id}".strip(),
        [
            ("synapse.policy.decision", "denied"),
            ("synapse.policy.reason", str(payload.get("reason_code", ""))),
            ("synapse.claimant", str(payload.get("claimant", ""))),
        ],
    )


def _text(payload: Mapping[str, object], key: str) -> str | None:
    """Return a non-empty text field, or ``None``."""
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _count(value: object) -> int | None:
    """Return a non-negative integer that is not a boolean, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


_FIELDS: Mapping[str, tuple[tuple[str, str], ...]] = {
    EventKind.OPERATION_DECLARED: (("effect", "synapse.operation.effect"),),
    EventKind.OPERATION_OUTCOME: (
        ("state", "synapse.outcome.state"),
        ("boundary", "synapse.outcome.boundary"),
        ("external_effect", "synapse.outcome.external_effect"),
    ),
    EventKind.OPERATION_VERIFIED: (
        ("verifier", "synapse.verification.verifier"),
        ("communication", "synapse.verification.communication"),
        ("effect", "synapse.verification.effect"),
        ("result", "synapse.verification.result"),
    ),
}
"""Journal field → contract key, per operation row kind; targets are never read."""


def _operation(event: StoredEvent) -> _Read | Literal[False]:
    """Read one operation row, or ``False`` when a field the hub writes is missing."""
    payload = event.payload
    task_id = _text(payload, "task_id")
    operation_id = _text(payload, "operation_id")
    sender = _text(payload, "sender")
    values = {key: _text(payload, field) for field, key in _FIELDS[event.kind]}
    if task_id is None or operation_id is None or sender is None or None in values.values():
        return False
    attributes: list[tuple[str, PointAttributeValue | None]] = [
        ("synapse.operation.id", operation_id),
        ("synapse.operation.sender", sender),
        *values.items(),
    ]
    if event.kind == EventKind.OPERATION_DECLARED:
        epoch = _count(payload.get("snapshot_epoch"))
        budget = _count(payload.get("budget_remaining"))
        if epoch is None or ("budget_remaining" in payload and budget is None):
            return False
        attributes += [
            ("synapse.operation.snapshot_epoch", epoch),
            ("synapse.operation.budget_remaining", budget),
        ]
        family, name = FAMILY_OPERATION, f"operation {operation_id}"
    elif event.kind == EventKind.OPERATION_OUTCOME:
        family, name = FAMILY_OUTCOME, f"operation outcome {values['synapse.outcome.state']}"
    else:
        effect = values["synapse.verification.effect"]
        result = values["synapse.verification.result"]
        family, name = FAMILY_VERIFICATION, f"operation verified {effect} {result}"
    return _Read(event, task_id, family, name, attributes, operation_id)


def _resolve(reads: list[_Read]) -> list[CorrelationPoint]:
    """Attach children to their declaration and stretch each operation to its last row."""
    declarations: dict[tuple[str, str], int] = {}
    for index, read in enumerate(reads):
        if read.family == FAMILY_OPERATION:
            declarations.setdefault((read.task_id, read.operation_id), index)
    ends = [read.event.ts for read in reads]
    parents: list[int | None] = [None] * len(reads)
    for index, read in enumerate(reads):
        if read.family not in {FAMILY_OUTCOME, FAMILY_VERIFICATION}:
            continue
        owner = declarations.get((read.task_id, read.operation_id))
        if owner is not None:
            parents[index] = reads[owner].event.seq
            ends[owner] = max(ends[owner], read.event.ts)
    return [
        CorrelationPoint(
            task_id=read.task_id,
            seq=read.event.seq,
            family=read.family,
            name=read.name,
            start_ts=read.event.ts,
            end_ts=ends[index],
            parent_seq=parents[index],
            attributes=_attributes(read),
        )
        for index, read in enumerate(reads)
    ]


def _attributes(read: _Read) -> tuple[tuple[str, PointAttributeValue], ...]:
    """Return the row's attributes plus the common keys, sorted, without empty values.

    A count of ``0`` is a recorded value and is kept.
    """
    pairs = [
        *read.attributes,
        ("synapse.kind", read.event.kind),
        ("synapse.seq", str(read.event.seq)),
        ("synapse.task_id", read.task_id),
    ]
    present = [(key, value) for key, value in pairs if value is not None and value != ""]
    return tuple(sorted(present, key=lambda pair: pair[0]))
