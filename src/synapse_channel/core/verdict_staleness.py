# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — derive verdict staleness from evidence, verdict and correction records
"""Decide which verdicts rest on a corrected input, independent of record order.

The fold reads the recorded ``evidence``, ``verdict`` and ``correction`` notes
(see :mod:`synapse_channel.core.verdict_records`) and returns one state per
verdict and one gate decision per subject. It is a pure function of the *set*
of records: no timestamp or arrival order enters it, so a replayed log, a
checkpointed log and a federated peer reach the same answer.

An object's identity is the pair (namespace, SHA-256). Records in one namespace
never reach objects or verdicts of another, so a seat cannot affect another
project by declaring the same hash in its own namespace. Authority comes from
the caller's :class:`CorrectionPolicy`, always for the record's own namespace:

* an ``evidence`` declaration and a ``verdict`` count only when their seat may
  write that namespace (its own project, or an allow-listed seat);
* a ``correction`` counts only when its seat declared the corrected object in
  that namespace, or may correct there (an allow-listed seat). An object nobody
  declared cannot be corrected.

A verdict is **stale** when any input it depends on has a counted correction the
verdict does not acknowledge. Its dependencies are its direct inputs, the
members of declared manifests among them and, transitively, the inputs of each
reviewer's latest verdict on those inputs. A correction stays in force
permanently, so a revert R -> R' -> R leaves both objects corrected until
acknowledged. An ``accepted`` verdict that cites no input is
**inputs-unrecorded**, and a dependency chain deeper than
:data:`MAX_DEPENDENCY_DEPTH` is **unbounded**.

The gate passes a subject only when it has at least one verdict and every
reviewer's latest verdict (the highest ``reviewer_sequence``) is ``accepted``
and ``fresh``. Unbounded, conflicting and, by default, inputs-unrecorded
verdicts fail closed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from synapse_channel.core.verdict_records import (
    CORRECTION_NOTE_KIND,
    EVIDENCE_NOTE_KIND,
    VERDICT_NOTE_KIND,
    CorrectionRecord,
    VerdictRecord,
    VerdictRecordError,
    correction_id,
    parse_correction,
    parse_evidence,
    parse_verdict,
)

MAX_DEPENDENCY_DEPTH = 32
"""Deepest verdict-on-verdict chain followed before a verdict is unbounded."""

STATE_FRESH = "fresh"
STATE_STALE = "stale"
STATE_INPUTS_UNRECORDED = "inputs-unrecorded"
STATE_UNBOUNDED = "unbounded"

ObjectKey = tuple[str, str]
"""An object's identity: (namespace, SHA-256)."""


@dataclass(frozen=True)
class RecordedNote:
    """One progress note as the log holds it: sequence, bound author, kind, body."""

    seq: int
    author: str
    kind: str
    text: str


@dataclass(frozen=True)
class CorrectionPolicy:
    """Who may write and correct records, per namespace.

    ``may_write(namespace, seat)`` grants declaring objects and recording verdicts
    there; ``may_correct(namespace, seat)`` grants correcting objects there beyond
    the object's own declarers.
    """

    may_write: Callable[[str, str], bool]
    may_correct: Callable[[str, str], bool]


@dataclass(frozen=True)
class VerdictState:
    """The derived state of one recorded verdict."""

    seq: int
    reviewer: str
    record: VerdictRecord
    state: str
    unacknowledged_corrections: tuple[str, ...]


@dataclass(frozen=True)
class GateDecision:
    """Whether a subject may be approved, and why not when it may not."""

    namespace: str
    subject_sha256: str
    passed: bool
    reasons: tuple[str, ...]


@dataclass
class VerdictFold:
    """Counted records and derived verdict states for one log."""

    declarers: dict[ObjectKey, set[str]] = field(default_factory=dict)
    members: dict[ObjectKey, set[str]] = field(default_factory=dict)
    corrections: dict[ObjectKey, dict[str, CorrectionRecord]] = field(default_factory=dict)
    refused: list[tuple[int, str]] = field(default_factory=list)
    verdicts: list[VerdictState] = field(default_factory=list)

    def corrections_of(self, key: ObjectKey) -> dict[str, CorrectionRecord]:
        """Return counted corrections of the object ``key`` keyed by correction id."""
        return self.corrections.get(key, {})


_PARSERS: dict[str, Callable[[str], object]] = {
    EVIDENCE_NOTE_KIND: parse_evidence,
    CORRECTION_NOTE_KIND: parse_correction,
    VERDICT_NOTE_KIND: parse_verdict,
}


def _sorted_kind(notes: list[RecordedNote], kind: str) -> list[RecordedNote]:
    return sorted((note for note in notes if note.kind == kind), key=lambda note: note.seq)


def _parsed_notes(
    notes: Iterable[RecordedNote], refused: list[tuple[int, str]]
) -> list[RecordedNote]:
    """Keep verdict-family notes whose body parses; refuse the others."""
    kept: list[RecordedNote] = []
    for note in notes:
        parser = _PARSERS.get(note.kind)
        if parser is None:
            continue
        try:
            parser(note.text)
        except VerdictRecordError:
            refused.append((note.seq, "malformed-record"))
            continue
        kept.append(note)
    return kept


def _count_evidence(fold: VerdictFold, notes: list[RecordedNote], policy: CorrectionPolicy) -> None:
    for note in _sorted_kind(notes, EVIDENCE_NOTE_KIND):
        record = parse_evidence(note.text)
        if not policy.may_write(record.namespace, note.author):
            fold.refused.append((note.seq, "evidence-not-authorised"))
            continue
        key = (record.namespace, record.sha256)
        fold.declarers.setdefault(key, set()).add(note.author)
        if record.members:
            fold.members.setdefault(key, set()).update(record.members)


def _count_corrections(
    fold: VerdictFold, notes: list[RecordedNote], policy: CorrectionPolicy
) -> None:
    for note in _sorted_kind(notes, CORRECTION_NOTE_KIND):
        record = parse_correction(note.text)
        key = (record.namespace, record.corrects)
        declared = note.author in fold.declarers.get(key, set())
        allowed = key in fold.declarers and policy.may_correct(record.namespace, note.author)
        if not (declared or allowed):
            fold.refused.append((note.seq, "correction-not-authorised"))
            continue
        fold.corrections.setdefault(key, {})[correction_id(record)] = record


def _latest_by_reviewer(
    entries: list[tuple[str, VerdictRecord]],
) -> dict[str, list[VerdictRecord]]:
    """Return each reviewer's verdicts at its highest sequence (ties kept together)."""
    latest: dict[str, list[VerdictRecord]] = {}
    for reviewer, record in entries:
        current = latest.get(reviewer)
        if current is None or record.reviewer_sequence > current[0].reviewer_sequence:
            latest[reviewer] = [record]
        elif record.reviewer_sequence == current[0].reviewer_sequence:
            current.append(record)
    return latest


def _dependencies(
    fold: VerdictFold,
    record: VerdictRecord,
    upstream: dict[ObjectKey, list[VerdictRecord]],
) -> tuple[set[ObjectKey], bool]:
    """Return every object a verdict depends on and whether the chain stayed bounded."""
    namespace = record.namespace
    seen: set[ObjectKey] = set()
    frontier = {(namespace, digest) for digest in record.inputs}
    depth = 0
    while frontier:
        if depth > MAX_DEPENDENCY_DEPTH:
            return seen, False
        seen |= frontier
        following: set[ObjectKey] = set()
        for key in frontier:
            following |= {(namespace, digest) for digest in fold.members.get(key, set())}
            for verdict in upstream.get(key, []):
                following |= {(namespace, digest) for digest in verdict.inputs}
        frontier = following - seen
        depth += 1
    return seen, True


def fold_verdicts(notes: Iterable[RecordedNote], policy: CorrectionPolicy) -> VerdictFold:
    """Count authorised records and derive every verdict's state.

    Declarations are counted before corrections and corrections before verdicts,
    whatever their sequence, so the result depends only on the record set.
    """
    fold = VerdictFold()
    kept = _parsed_notes(notes, fold.refused)
    _count_evidence(fold, kept, policy)
    _count_corrections(fold, kept, policy)
    counted: list[tuple[RecordedNote, VerdictRecord]] = []
    for note in _sorted_kind(kept, VERDICT_NOTE_KIND):
        record = parse_verdict(note.text)
        if not policy.may_write(record.namespace, note.author):
            fold.refused.append((note.seq, "verdict-not-authorised"))
            continue
        counted.append((note, record))
    by_subject: dict[ObjectKey, list[tuple[str, VerdictRecord]]] = {}
    for note, record in counted:
        key = (record.namespace, record.subject_sha256)
        by_subject.setdefault(key, []).append((note.author, record))
    upstream = {
        key: [verdict for group in _latest_by_reviewer(entries).values() for verdict in group]
        for key, entries in by_subject.items()
    }
    for note, record in counted:
        fold.verdicts.append(_derive_state(fold, note, record, upstream))
    fold.refused.sort()
    return fold


def _derive_state(
    fold: VerdictFold,
    note: RecordedNote,
    record: VerdictRecord,
    upstream: dict[ObjectKey, list[VerdictRecord]],
) -> VerdictState:
    dependencies, bounded = _dependencies(fold, record, upstream)
    acknowledged = set(record.acknowledged_corrections)
    open_corrections = sorted(
        identifier
        for key in dependencies
        for identifier in fold.corrections_of(key)
        if identifier not in acknowledged
    )
    if not bounded:
        state = STATE_UNBOUNDED
    elif open_corrections:
        state = STATE_STALE
    elif record.outcome == "accepted" and not record.inputs:
        state = STATE_INPUTS_UNRECORDED
    else:
        state = STATE_FRESH
    return VerdictState(
        seq=note.seq,
        reviewer=note.author,
        record=record,
        state=state,
        unacknowledged_corrections=tuple(open_corrections),
    )


def gate_decision(
    fold: VerdictFold,
    namespace: str,
    subject_sha256: str,
    *,
    allow_unrecorded_inputs: bool = False,
) -> GateDecision:
    """Decide whether object (``namespace``, ``subject_sha256``) may be approved.

    Each reviewer counts once, through the verdict with its highest
    ``reviewer_sequence``. Two different verdicts at that same sequence from one
    reviewer are a conflict and fail closed. Arrival order plays no part.
    """
    latest: dict[str, list[VerdictState]] = {}
    for state in fold.verdicts:
        record = state.record
        if (record.namespace, record.subject_sha256) != (namespace, subject_sha256):
            continue
        current = latest.get(state.reviewer)
        if current is None or record.reviewer_sequence > current[0].record.reviewer_sequence:
            latest[state.reviewer] = [state]
        elif record.reviewer_sequence == current[0].record.reviewer_sequence:
            current.append(state)
    if not latest:
        return GateDecision(namespace, subject_sha256, False, ("no verdict recorded",))
    reasons: list[str] = []
    for reviewer in sorted(latest):
        states = latest[reviewer]
        if len({item.record for item in states}) > 1:
            reasons.append(f"{reviewer}: conflicting verdicts at one sequence")
            continue
        reason = _verdict_reason(states[0], allow_unrecorded_inputs=allow_unrecorded_inputs)
        if reason is not None:
            reasons.append(f"{reviewer}: {reason}")
    return GateDecision(namespace, subject_sha256, not reasons, tuple(reasons))


def _verdict_reason(state: VerdictState, *, allow_unrecorded_inputs: bool) -> str | None:
    """Return why one reviewer's latest verdict blocks the gate, or ``None``."""
    if state.record.outcome != "accepted":
        return state.record.outcome
    if state.state == STATE_STALE:
        return "stale input, unacknowledged corrections " + ", ".join(
            state.unacknowledged_corrections
        )
    if state.state == STATE_UNBOUNDED:
        return f"dependency chain exceeds {MAX_DEPENDENCY_DEPTH} levels"
    if state.state == STATE_INPUTS_UNRECORDED and not allow_unrecorded_inputs:
        return "accepted without recorded inputs"
    return None
