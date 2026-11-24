# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only, per-task projection of operation journal records
"""Project journalled operation records into immutable, per-task recovery snapshots.

Out-of-tree recovery and monitoring consumers need the exact evidence a hub's
journal holds for a task's declared operations: declarations, attempt
lifecycles, executor outcomes, independent verifications, standing scope stops,
fence claim epochs, and any malformed rows encountered along the way.

:class:`OperationRecordReader` projects those rows purely and fail-closed,
sharing Core's marker recomputation and verification-marker matching helpers in
:mod:`~synapse_channel.core.operation_stop_readbacks`:

* **Per-task partitioning:** an unfiltered reader partitions task-scoped rows
  by ``payload["task_id"]`` and refuses to produce a merged multi-task
  snapshot; a filtered reader tracks only its named task. Scope-level
  ``operation_stopped`` and ``operation_resumed`` rows apply globally to every
  task, including tasks first observed later.
* **Unattributed operation rows:** an operation-kind row with no non-empty
  string ``task_id`` is not attributed to any task and never poisons unrelated
  tasks; its journal sequence is recorded in :meth:`OperationRecordReader.unattributed_seqs`.
* **Contradictions vs. verification disagreement:**
  :attr:`OperationRecordSnapshot.contradicted_seq` records only the first
  opposing marker or executor-claim contradiction (``confirmed_done`` vs.
  ``sealed``, ``not_started`` vs. ``applied``, or opposing matched marker
  blobs), not :meth:`~synapse_channel.core.operation_attempt_ledger.AttemptLedger.contradicted_at`.
  Verification disagreement (for example ``verified`` alongside ``absent``) is
  derived from :attr:`OperationRecordSnapshot.verified`,
  :attr:`OperationRecordSnapshot.partial_seqs`,
  :attr:`OperationRecordSnapshot.absent_seqs`, and
  :attr:`OperationRecordSnapshot.applied_readback_seqs`.
* **Kept stop read-backs:** both ``sealed`` and ``applied`` attempt updates
  from ``operation_stop_readback`` are gated on the kept read-back (matching
  :meth:`~synapse_channel.core.operation_stop_ledger.StopLedger.record_readback`,
  so a kept ``unsealable`` read-back is never overwritten by a later ``sealed``
  or ``applied`` read-back, whereas ``unread`` upgrades to either closing
  result). Note that the live hub's
  :class:`~synapse_channel.core.operation_attempt_ledger.AttemptLedger` marks
  attempts from a read-back's marker blob without consulting the kept
  read-back; this reader intentionally enforces the stricter kept-read-back
  invariant for recovery verdicts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import (
    OBSERVED_EFFECTS,
    TERMINAL_OUTCOME_STATES,
)
from synapse_channel.core.operation_attempts import APPROVED_FRESH
from synapse_channel.core.operation_stop_readbacks import (
    CLOSING_RESULTS,
    MARKER_UNSCOPED,
    READBACK_RESULTS,
    RESULT_APPLIED,
    RESULT_SEALED,
    RESULT_UNREAD,
    match_verification_marker,
    recomputed_marker_kind,
)
from synapse_channel.core.operation_stops import scopes_of

OPERATION_RECORD_KINDS: frozenset[str] = frozenset(
    {
        EventKind.OPERATION_DECLARED,
        EventKind.OPERATION_OUTCOME,
        EventKind.OPERATION_VERIFIED,
        EventKind.OPERATION_ATTEMPT_ANSWERED,
        EventKind.OPERATION_SEALED,
        EventKind.OPERATION_STOP_READBACK,
    }
)
"""Task-scoped operation journal event kinds projected by :class:`OperationRecordReader`."""

SCOPE_STOP_RECORD_KINDS: frozenset[str] = frozenset(
    {
        EventKind.OPERATION_STOPPED,
        EventKind.OPERATION_RESUMED,
    }
)
"""Scope-level stop and resume event kinds applied across every task."""

_NON_VERIFIED_RESULTS = frozenset({"not_verified", "not_checked"})

__all__ = [
    "IndependentVerificationRecord",
    "OPERATION_RECORD_KINDS",
    "OperationAttemptSnapshot",
    "OperationEvent",
    "OperationEvidenceSnapshot",
    "OperationMalformedRecord",
    "OperationRecordReader",
    "OperationRecordSnapshot",
    "SCOPE_STOP_RECORD_KINDS",
]


class OperationEvent(Protocol):
    """Structural contract for one journal event passed to ``feed_event``."""

    @property
    def kind(self) -> str:
        """Return the event kind string."""

    @property
    def seq(self) -> int:
        """Return the positive journal sequence number."""

    @property
    def payload(self) -> Mapping[str, Any]:
        """Return the event payload mapping."""


@dataclass(frozen=True, slots=True)
class OperationAttemptSnapshot:
    """Immutable view of one opened attempt of an operation."""

    attempt_id: str
    executor: str
    seq: int
    ended: bool
    sealed: bool
    applied: bool
    reconciled: bool


@dataclass(frozen=True, slots=True)
class IndependentVerificationRecord:
    """One independent verification confirming an operation's effect."""

    seq: int
    verifier: str
    observation: str


@dataclass(frozen=True, slots=True)
class OperationRecordSnapshot:
    """Immutable projection of every journalled fact about one declared or referenced operation.

    Attributes
    ----------
    contradicted_seq : int or None
        Journal sequence of the first opposing marker or executor-claim
        contradiction (``confirmed_done`` vs. ``sealed``, ``not_started`` vs.
        ``applied``, or opposing matched marker blobs), or ``None`` when none
        occurred. This is intentionally narrower than
        :meth:`~synapse_channel.core.operation_attempt_ledger.AttemptLedger.contradicted_at`:
        a ``not_verified`` row after ``verified`` leaves ``contradicted_seq``
        as ``None``, and verification disagreement is derived from
        ``verified``, ``partial_seqs``, ``absent_seqs``, and
        ``applied_readback_seqs``.
    """

    declared_seq: int | None
    snapshot_epoch: int | None
    budget: int | None
    declarer: str | None
    effect: str | None
    intent_sha256: str | None
    scopes: tuple[str, ...]
    attempts: tuple[OperationAttemptSnapshot, ...]
    applied_readback_seqs: tuple[int, ...]
    contradicted_seq: int | None
    verified: tuple[IndependentVerificationRecord, ...]
    partial_seqs: tuple[int, ...]
    absent_seqs: tuple[int, ...]
    unknown_seqs: tuple[int, ...]

    @property
    def checked(self) -> bool:
        """Return whether any independent verification row exists for this operation."""
        return bool(self.verified or self.partial_seqs or self.absent_seqs or self.unknown_seqs)


@dataclass(frozen=True, slots=True)
class OperationMalformedRecord:
    """One malformed operation or scope-stop journal row captured fail-closed."""

    seq: int
    kind: str
    detail: str
    scope_level: bool


@dataclass(frozen=True, slots=True)
class OperationEvidenceSnapshot:
    """Immutable per-task snapshot produced by :meth:`OperationRecordReader.snapshot`."""

    operations: Mapping[str, OperationRecordSnapshot]
    standing_stops: Mapping[str, int]
    claim_epochs: Mapping[int, int]
    malformed: tuple[OperationMalformedRecord, ...]

    @property
    def present(self) -> bool:
        """Return whether the task has any operation record or task-level malformed row.

        A ``scope_level=True`` malformed record alone does not mark an
        unrelated task without operation records as present.
        """
        return bool(self.operations or any(not item.scope_level for item in self.malformed))


@dataclass(slots=True)
class _AttemptBucket:
    """Mutable working state for one attempt while rows are projected."""

    attempt_id: str
    executor: str
    seq: int
    ended: bool = False
    sealed: bool = False
    applied: bool = False
    reconciled: bool = False
    marker_observed: bool = False
    verified_applied: bool = False
    verified_sealed: bool = False
    claimed_done: bool = False
    claimed_none: bool = False


@dataclass(slots=True)
class _OperationBucket:
    """Mutable working state for one operation within a single task."""

    declared_seq: int | None = None
    snapshot_epoch: int | None = None
    budget: int | None = None
    declarer: str | None = None
    effect: str | None = None
    intent_sha256: str | None = None
    scopes: tuple[str, ...] = ()
    attempts: list[_AttemptBucket] = field(default_factory=list)
    applied_readback_seqs: list[int] = field(default_factory=list)
    contradicted_seq: int | None = None
    verified: list[tuple[int, str, str, str]] = field(default_factory=list)
    partial: list[tuple[int, str]] = field(default_factory=list)
    absent: list[tuple[int, str]] = field(default_factory=list)
    unknown: list[tuple[int, str]] = field(default_factory=list)


@dataclass(slots=True)
class _TaskBucket:
    """Mutable working state for one task's operation records and claim epochs."""

    operations: dict[str, _OperationBucket] = field(default_factory=dict)
    claim_epochs: dict[int, int] = field(default_factory=dict)
    malformed: list[OperationMalformedRecord] = field(default_factory=list)


def _text(value: object) -> str | None:
    """Return ``value`` if it is a non-empty string, else ``None``."""
    return value if isinstance(value, str) and value else None


def _whole(value: object) -> int | None:
    """Return ``value`` if it is a non-negative non-bool integer, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _mark_ended(attempt: _AttemptBucket) -> None:
    """Mark ``attempt`` ended and reconcile it if its marker was already observed."""
    attempt.ended = True
    if attempt.marker_observed:
        attempt.reconciled = True


def _record_contradiction(entry: _OperationBucket, seq: int) -> None:
    """Record the first opposing marker or executor-claim contradiction sequence."""
    if entry.contradicted_seq is None:
        entry.contradicted_seq = seq


def _apply_declared(
    seq: int,
    kind: str,
    operation_id: str,
    payload: Mapping[str, Any],
    entry: _OperationBucket,
    bucket: _TaskBucket,
) -> None:
    """Project one ``operation_declared`` row into ``entry``."""
    epoch = _whole(payload.get("snapshot_epoch"))
    if epoch is None:
        bucket.malformed.append(
            OperationMalformedRecord(
                seq, kind, "a declaration has no usable snapshot epoch", scope_level=False
            )
        )
        return
    budget: int | None = None
    if "budget_remaining" in payload:
        budget = _whole(payload.get("budget_remaining"))
        if budget is None:
            bucket.malformed.append(
                OperationMalformedRecord(
                    seq, kind, "a declaration has an unusable budget", scope_level=False
                )
            )
            return
    sender = _text(payload.get("sender"))
    if sender is None:
        bucket.malformed.append(
            OperationMalformedRecord(
                seq, kind, "a declaration has no usable sender", scope_level=False
            )
        )
        return
    if entry.declared_seq is None:
        entry.declared_seq = seq
        entry.snapshot_epoch = epoch
        entry.budget = budget
        entry.declarer = sender
        entry.effect = _text(payload.get("effect"))
        entry.intent_sha256 = _text(payload.get("intent_sha256"))
        entry.scopes = scopes_of(operation_id, sender, entry.effect)


def _apply_outcome(seq: int, payload: Mapping[str, Any], entry: _OperationBucket) -> None:
    """Project one ``operation_outcome`` row into ``entry``."""
    sender = _text(payload.get("sender"))
    if sender is None:
        return
    state = _text(payload.get("state")) or ""
    external_effect = _text(payload.get("external_effect")) or ""
    last = next(
        (attempt for attempt in reversed(entry.attempts) if attempt.executor == sender),
        None,
    )
    if last is not None:
        if "confirmed_done" in (state, external_effect):
            last.claimed_done = True
            if last.sealed:
                _record_contradiction(entry, seq)
        if "not_started" in (state, external_effect):
            last.claimed_none = True
            if last.applied:
                _record_contradiction(entry, seq)
    if state not in TERMINAL_OUTCOME_STATES:
        return
    for attempt in entry.attempts:
        if not attempt.ended and attempt.executor == sender:
            _mark_ended(attempt)


def _apply_attempt_answered(
    seq: int,
    kind: str,
    payload: Mapping[str, Any],
    entry: _OperationBucket,
    bucket: _TaskBucket,
) -> None:
    """Project one ``operation_attempt_answered`` row into ``entry``."""
    answer = _text(payload.get("answer"))
    if answer is None:
        bucket.malformed.append(
            OperationMalformedRecord(
                seq, kind, "an attempt answer has no usable answer", scope_level=False
            )
        )
        return
    if answer != APPROVED_FRESH:
        return
    sender = _text(payload.get("sender"))
    attempt_id = _text(payload.get("attempt_id"))
    if sender is None or attempt_id is None:
        bucket.malformed.append(
            OperationMalformedRecord(
                seq, kind, "an opened attempt lacks sender or attempt_id", scope_level=False
            )
        )
        return
    entry.attempts.append(_AttemptBucket(attempt_id=attempt_id, executor=sender, seq=seq))


def _apply_sealed(
    seq: int,
    kind: str,
    payload: Mapping[str, Any],
    entry: _OperationBucket,
    bucket: _TaskBucket,
) -> None:
    """Project one ``operation_sealed`` row into ``entry``."""
    attempt_id = _text(payload.get("attempt_id"))
    if attempt_id is None:
        bucket.malformed.append(
            OperationMalformedRecord(seq, kind, "a seal record names no attempt", scope_level=False)
        )
        return
    for attempt in entry.attempts:
        if attempt.attempt_id == attempt_id and not attempt.ended:
            attempt.sealed = True
            _mark_ended(attempt)
            if attempt.claimed_done or attempt.verified_applied:
                _record_contradiction(entry, seq)


def _reconcile_verified_attempts(
    seq: int,
    operation_id: str,
    effect: str,
    payload: Mapping[str, Any],
    entry: _OperationBucket,
) -> None:
    """Reconcile attempts and detect opposing marker blobs for an independent verification."""
    raw_evidence = payload.get("evidence")
    evidence = raw_evidence if isinstance(raw_evidence, Mapping) else None
    match_kind, matched_id, blob_kind = match_verification_marker(
        operation_id,
        entry.intent_sha256,
        [attempt.attempt_id for attempt in entry.attempts],
        effect,
        evidence,
    )
    if matched_id is not None:
        matched = next(attempt for attempt in entry.attempts if attempt.attempt_id == matched_id)
        matched.marker_observed = True
        if blob_kind == RESULT_APPLIED:
            matched.verified_applied = True
            if matched.sealed:
                _record_contradiction(entry, seq)
        else:
            matched.verified_sealed = True
            if matched.applied:
                _record_contradiction(entry, seq)
        if matched.ended:
            matched.reconciled = True
    elif match_kind == MARKER_UNSCOPED:
        for attempt in entry.attempts:
            if attempt.ended:
                attempt.reconciled = True


def _apply_verified(
    seq: int,
    kind: str,
    operation_id: str,
    payload: Mapping[str, Any],
    entry: _OperationBucket,
    bucket: _TaskBucket,
) -> None:
    """Project one ``operation_verified`` row into ``entry``."""
    effect, result = payload.get("effect"), payload.get("result")
    valid = (effect == "observed" and result == "verified") or (
        effect in ("observed", "absent", "unknown") and result in _NON_VERIFIED_RESULTS
    )
    if not valid:
        bucket.malformed.append(
            OperationMalformedRecord(
                seq, kind, "a verification has an unknown effect or result", scope_level=False
            )
        )
        return
    sender = _text(payload.get("sender"))
    if sender is None:
        bucket.malformed.append(
            OperationMalformedRecord(
                seq, kind, "a verification has no usable sender", scope_level=False
            )
        )
        return
    verifier = _text(payload.get("verifier")) or "a verifier"
    observation = _text(payload.get("observation")) or "no observation"
    if effect == "observed" and result == "verified":
        entry.verified.append((seq, sender, verifier, observation))
    elif effect == "observed":
        entry.partial.append((seq, sender))
    elif effect == "absent":
        entry.absent.append((seq, sender))
    else:
        entry.unknown.append((seq, sender))
    if entry.declarer is not None and sender != entry.declarer and effect in OBSERVED_EFFECTS:
        _reconcile_verified_attempts(seq, operation_id, str(effect), payload, entry)


def _apply_kept_readback(
    seq: int,
    attempt_id: str,
    result: str,
    kept: str | None,
    entry: _OperationBucket,
) -> None:
    """Update attempts from a closing stop read-back only when it is the kept read-back."""
    if result == RESULT_SEALED and kept == RESULT_SEALED:
        for attempt in entry.attempts:
            if attempt.attempt_id == attempt_id and not attempt.applied:
                attempt.sealed = True
                _mark_ended(attempt)
                if attempt.claimed_done or attempt.verified_applied:
                    _record_contradiction(entry, seq)
    elif result == RESULT_APPLIED and kept == RESULT_APPLIED:
        entry.applied_readback_seqs.append(seq)
        for attempt in entry.attempts:
            if attempt.attempt_id == attempt_id and not attempt.sealed:
                attempt.applied = True
                _mark_ended(attempt)
                if attempt.claimed_none or attempt.verified_sealed:
                    _record_contradiction(entry, seq)


def _freeze_operation(entry: _OperationBucket) -> OperationRecordSnapshot:
    """Materialise one immutable :class:`OperationRecordSnapshot`."""
    declarer = entry.declarer
    return OperationRecordSnapshot(
        declared_seq=entry.declared_seq,
        snapshot_epoch=entry.snapshot_epoch,
        budget=entry.budget,
        declarer=declarer,
        effect=entry.effect,
        intent_sha256=entry.intent_sha256,
        scopes=entry.scopes,
        attempts=tuple(
            OperationAttemptSnapshot(
                attempt_id=attempt.attempt_id,
                executor=attempt.executor,
                seq=attempt.seq,
                ended=attempt.ended,
                sealed=attempt.sealed,
                applied=attempt.applied,
                reconciled=attempt.reconciled,
            )
            for attempt in entry.attempts
        ),
        applied_readback_seqs=tuple(entry.applied_readback_seqs),
        contradicted_seq=entry.contradicted_seq,
        verified=tuple(
            IndependentVerificationRecord(seq, verifier, observation)
            for seq, sender, verifier, observation in entry.verified
            if sender != declarer
        ),
        partial_seqs=tuple(seq for seq, sender in entry.partial if sender != declarer),
        absent_seqs=tuple(seq for seq, sender in entry.absent if sender != declarer),
        unknown_seqs=tuple(seq for seq, sender in entry.unknown if sender != declarer),
    )


class OperationRecordReader:
    """Read-only, per-task projector for operation and scope-stop journal records.

    Parameters
    ----------
    task_id : str or None, optional
        When given, the reader projects only rows belonging to ``task_id``
        (plus global scope-level stop and resume rows). When ``None``, the
        reader partitions task-scoped rows into one bucket per ``task_id`` and
        requires ``task_id`` to be named on every :meth:`snapshot` call.

    Raises
    ------
    ValueError
        If ``task_id`` is provided as an empty or non-string value.
    """

    def __init__(self, *, task_id: str | None = None) -> None:
        if task_id is not None and _text(task_id) is None:
            raise ValueError("task_id must be a non-empty string")
        self._task_id: str | None = task_id
        self._buckets: dict[str, _TaskBucket] = {}
        self._standing_stops: dict[str, int] = {}
        self._stop_listed: dict[int, frozenset[tuple[str, str]]] = {}
        self._stop_readbacks: dict[tuple[int, str], str] = {}
        self._scope_malformed: list[OperationMalformedRecord] = []
        self._unattributed_seqs: list[int] = []

    def feed(self, kind: str, seq: int, payload: Mapping[str, Any]) -> None:
        """Feed one journal row into the projection.

        Parameters
        ----------
        kind : str
            Journal event kind.
        seq : int
            Positive journal sequence number.
        payload : Mapping[str, Any]
            Event payload mapping.

        Raises
        ------
        ValueError
            If ``seq`` is not a positive non-bool integer.
        """
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise ValueError("seq must be a positive integer")
        if kind in SCOPE_STOP_RECORD_KINDS:
            self._apply_scope_stop(kind, seq, payload)
            return
        if kind == EventKind.CLAIM:
            self._apply_claim(seq, payload)
            return
        if kind not in OPERATION_RECORD_KINDS:
            return
        self._apply_operation_row(kind, seq, payload)

    def feed_event(self, event: OperationEvent) -> None:
        """Feed one journal event object into the projection."""
        self.feed(event.kind, event.seq, event.payload)

    def task_ids(self) -> tuple[str, ...]:
        """Return the sorted task identifiers observed by this reader."""
        return tuple(sorted(self._buckets))

    def unattributed_seqs(self) -> tuple[int, ...]:
        """Return sorted sequences of operation rows that lacked a usable ``task_id``."""
        return tuple(sorted(self._unattributed_seqs))

    def snapshot(self, task_id: str | None = None) -> OperationEvidenceSnapshot:
        """Return an immutable :class:`OperationEvidenceSnapshot` for one task.

        Parameters
        ----------
        task_id : str or None, optional
            Required on an unfiltered reader. On a filtered reader, defaults to
            the reader's task and raises :class:`ValueError` if a different
            task is requested.

        Raises
        ------
        ValueError
            If an unfiltered reader is asked for a merged snapshot
            (``task_id=None``), if ``task_id`` is empty or non-string, or if a
            filtered reader is asked for another task.
        """
        resolved = self._resolve_snapshot_task(task_id)
        bucket = self._buckets.get(resolved)
        if bucket is None:
            return OperationEvidenceSnapshot(
                operations=MappingProxyType({}),
                standing_stops=MappingProxyType(dict(self._standing_stops)),
                claim_epochs=MappingProxyType({}),
                malformed=tuple(self._scope_malformed),
            )
        return OperationEvidenceSnapshot(
            operations=MappingProxyType(
                {
                    operation_id: _freeze_operation(entry)
                    for operation_id, entry in bucket.operations.items()
                }
            ),
            standing_stops=MappingProxyType(dict(self._standing_stops)),
            claim_epochs=MappingProxyType(dict(bucket.claim_epochs)),
            malformed=tuple(bucket.malformed) + tuple(self._scope_malformed),
        )

    def _resolve_snapshot_task(self, task_id: str | None) -> str:
        if self._task_id is None:
            if task_id is None:
                raise ValueError("task_id is required on an unfiltered reader")
            if _text(task_id) is None:
                raise ValueError("task_id must be a non-empty string")
            return task_id
        if task_id is None:
            return self._task_id
        if task_id != self._task_id:
            raise ValueError(f"reader is filtered to task {self._task_id!r}")
        return self._task_id

    def _apply_claim(self, seq: int, payload: Mapping[str, Any]) -> None:
        row_task = _text(payload.get("task_id"))
        if row_task is None or (self._task_id is not None and row_task != self._task_id):
            return
        epoch = _whole(payload.get("epoch"))
        if epoch:
            bucket = self._buckets.setdefault(row_task, _TaskBucket())
            bucket.claim_epochs[seq] = epoch

    def _apply_scope_stop(self, kind: str, seq: int, payload: Mapping[str, Any]) -> None:
        scope = _text(payload.get("scope"))
        if scope is None:
            self._scope_malformed.append(
                OperationMalformedRecord(
                    seq, kind, "a scope stop record names no scope", scope_level=True
                )
            )
            return
        if kind == EventKind.OPERATION_RESUMED:
            self._standing_stops.pop(scope, None)
            return
        listed_raw = payload.get("listed")
        if listed_raw is not None and not isinstance(listed_raw, list):
            self._scope_malformed.append(
                OperationMalformedRecord(
                    seq, kind, "a stop record has an unusable listed field", scope_level=True
                )
            )
            return
        pairs: set[tuple[str, str]] = set()
        for item in listed_raw or ():
            if not isinstance(item, Mapping):
                self._scope_malformed.append(
                    OperationMalformedRecord(
                        seq,
                        kind,
                        "a stop record carries a malformed listed entry",
                        scope_level=True,
                    )
                )
                return
            op_id = _text(item.get("operation_id"))
            att_id = _text(item.get("attempt_id"))
            if op_id is None or att_id is None:
                self._scope_malformed.append(
                    OperationMalformedRecord(
                        seq,
                        kind,
                        "a stop record carries an incomplete listed entry",
                        scope_level=True,
                    )
                )
                return
            pairs.add((op_id, att_id))
        self._standing_stops[scope] = seq
        self._stop_listed[seq] = frozenset(pairs)

    def _apply_operation_row(self, kind: str, seq: int, payload: Mapping[str, Any]) -> None:
        row_task = _text(payload.get("task_id"))
        if row_task is None:
            self._unattributed_seqs.append(seq)
            return
        if self._task_id is not None and row_task != self._task_id:
            return
        bucket = self._buckets.setdefault(row_task, _TaskBucket())
        operation_id = _text(payload.get("operation_id"))
        if operation_id is None:
            bucket.malformed.append(
                OperationMalformedRecord(
                    seq, kind, "an operation record names no operation", scope_level=False
                )
            )
            return
        entry = bucket.operations.setdefault(operation_id, _OperationBucket())
        if kind == EventKind.OPERATION_DECLARED:
            _apply_declared(seq, kind, operation_id, payload, entry, bucket)
        elif kind == EventKind.OPERATION_OUTCOME:
            _apply_outcome(seq, payload, entry)
        elif kind == EventKind.OPERATION_VERIFIED:
            _apply_verified(seq, kind, operation_id, payload, entry, bucket)
        elif kind == EventKind.OPERATION_ATTEMPT_ANSWERED:
            _apply_attempt_answered(seq, kind, payload, entry, bucket)
        elif kind == EventKind.OPERATION_SEALED:
            _apply_sealed(seq, kind, payload, entry, bucket)
        else:
            self._apply_stop_readback(seq, kind, operation_id, payload, entry, bucket)

    def _apply_stop_readback(
        self,
        seq: int,
        kind: str,
        operation_id: str,
        payload: Mapping[str, Any],
        entry: _OperationBucket,
        bucket: _TaskBucket,
    ) -> None:
        stop_seq = payload.get("stop_seq")
        attempt_id = _text(payload.get("attempt_id"))
        result = _text(payload.get("result"))
        if (
            isinstance(stop_seq, bool)
            or not isinstance(stop_seq, int)
            or stop_seq <= 0
            or attempt_id is None
            or result not in READBACK_RESULTS
        ):
            bucket.malformed.append(
                OperationMalformedRecord(
                    seq,
                    kind,
                    "a stop read-back has an unusable stop, attempt or result",
                    scope_level=False,
                )
            )
            return
        listed = self._stop_listed.get(stop_seq)
        if listed is None or (operation_id, attempt_id) not in listed:
            bucket.malformed.append(
                OperationMalformedRecord(
                    seq,
                    kind,
                    "a stop read-back names an attempt the stop did not list",
                    scope_level=False,
                )
            )
            return
        if result in CLOSING_RESULTS and not self._marker_matches_readback(
            seq, kind, operation_id, attempt_id, result, payload, entry, bucket
        ):
            return
        key = (stop_seq, attempt_id)
        current = self._stop_readbacks.get(key)
        if current is None or (current == RESULT_UNREAD and result in CLOSING_RESULTS):
            self._stop_readbacks[key] = result
        _apply_kept_readback(seq, attempt_id, result, self._stop_readbacks.get(key), entry)

    def _marker_matches_readback(
        self,
        seq: int,
        kind: str,
        operation_id: str,
        attempt_id: str,
        result: str,
        payload: Mapping[str, Any],
        entry: _OperationBucket,
        bucket: _TaskBucket,
    ) -> bool:
        marker_ref = _text(payload.get("marker_ref"))
        object_format = _text(payload.get("object_format"))
        marker_object = _text(payload.get("marker_object"))
        if marker_ref is None or object_format is None or marker_object is None:
            bucket.malformed.append(
                OperationMalformedRecord(
                    seq, kind, "a closing stop read-back lacks marker fields", scope_level=False
                )
            )
            return False
        recomputed = recomputed_marker_kind(
            operation_id=operation_id,
            attempt_id=attempt_id,
            intent_sha256=entry.intent_sha256,
            marker_ref=marker_ref,
            object_format=object_format,
            marker_object=marker_object,
        )
        if recomputed != result:
            bucket.malformed.append(
                OperationMalformedRecord(
                    seq,
                    kind,
                    "a stop read-back marker does not match the declaration",
                    scope_level=False,
                )
            )
            return False
        return True
