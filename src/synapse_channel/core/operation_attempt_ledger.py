# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the attempts of each declared operation, rebuilt from the journal
"""Keep, per declared operation, its attempts and what ended and observed them.

The ledger is rebuilt from the journal when the hub starts and changes only
under the hub's mutation actor, in journal order:

* a declaration records the declarer and, with an intent, its digest and
  deadline; a second declaration of an id changes nothing;
* an ``approved_fresh`` answer opens an attempt bound to the approval it
  consumed;
* an outcome from the attempt's own executor whose state is
  ``not_started``, ``confirmed_done`` or ``confirmed_failed`` ends it; an
  outcome from any other sender ends nothing, because a third party cannot
  commit the executor to stop;
* a seal (``operation_sealed``) ends an open attempt without its executor: the
  sealer created the attempt's ``sealed`` marker at the system of record, so its
  effect can never land; the seal does not reconcile it;
* a kept ``applied`` stop read-back whose marker recomputes to ``applied`` ends
  its listed attempt as effect-present (``applied``, not ``sealed``) without
  reconciling or closing the operation, and keeps the operation from opening a
  second attempt;
* an independent verification (sender other than the declarer) with effect
  ``observed`` or ``absent`` reconciles attempts in one of three cases:

  - **unscoped** (no non-empty ``marker_object`` in ``evidence``): reconciles
    every attempt that has already ended, and never pre-reconciles an open one;
  - **matched** (``marker_object`` recomputes from ``(operation_id, attempt_id,
    intent_sha256)`` to one attempt's ``applied`` blob, or to its ``sealed``
    blob with ``effect == "absent"``): binds exclusively to that attempt,
    marking it reconciled immediately if ended or recording it as
    marker-observed without ending or sealing it so it reconciles the instant
    any end record arrives, and never reconciling another attempt;
  - **mismatched** (``marker_object`` is present yet fails recomputation, or
    pairs a ``sealed`` blob with ``observed``): reconciles nothing, with no
    fallback to the blanket rule;
* a verification by a sender other than the declarer with the result
  ``verified`` closes the operation.

A verification by the declarer is kept by the journal and counts for nothing
here. Restoring checks no time.

The ledger also keeps each scope's stops (:mod:`~synapse_channel.core.operation_stops`):

* an ``operation_stopped`` row stops its scope, at its epoch, and names the
  operations whose attempts were neither sealed nor reconciled in it;
* an ``operation_resumed`` row lifts the stop of its scope;
* the journal sequence of a scope's latest stop or resume is its line;
* an ``operation_stop_readback`` row records what a stop read back for one
  attempt it listed. Only a ``sealed`` or ``applied`` read-back closes the
  attempt for that stop; an executor's report never does. When a read-back's
  marker fields recompute to ``sealed``, the attempt is marked sealed, and any
  ``confirmed_done`` claim on it is recorded as a contradiction; when they
  recompute to ``applied``, the attempt ends as ``applied`` (not sealed), and
  any ``not_started`` claim on it is recorded as a contradiction;
* an ``operation_level`` row sets its scope's autonomy level at its epoch; the
  journal sequence of a scope's latest level row is its level line;
* a ``level_criteria`` row states what a raise of a scope must show, and a
  ``level_judgement`` row whether a judge found it met. The counts they rely on
  are computed from the facts above each time they are asked for;
* an ``operation_trigger`` row marks its operation or its stop, so that the hub
  writes no trigger twice, even after a restart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempts import APPROVED_FRESH, OperationFact
from synapse_channel.core.operation_level_criteria import (
    ITEM_INCORRECT_ACCEPTANCES,
    ITEM_STOPS_AT_REST,
    ITEM_UNKNOWN_OUTCOMES,
    ITEM_VERIFIED_EFFECTS,
    CriteriaItem,
)
from synapse_channel.core.operation_level_ledger import (
    LevelCriteria,
    LevelJudgement,
    LevelLedger,
    _ScopeLevel,
)
from synapse_channel.core.operation_levels import DEFAULT_LEVEL, LEVELS, lowest
from synapse_channel.core.operation_stop_ledger import StopLedger, _ScopeStops, _StopRow
from synapse_channel.core.operation_stop_readbacks import (
    MARKER_UNSCOPED,
    RESULT_APPLIED,
    RESULT_SEALED,
    marker_reads_applied,
    marker_reads_sealed,
    match_verification_marker,
)
from synapse_channel.core.operation_stop_states import STATE_AT_REST, StopState
from synapse_channel.core.operation_stops import scopes_of
from synapse_channel.core.operation_trigger_timing import (
    actor_trigger as _actor_trigger,
)
from synapse_channel.core.operation_trigger_timing import (
    due_triggers as _due_triggers,
)
from synapse_channel.core.operation_trigger_timing import (
    operation_trigger as _operation_trigger,
)
from synapse_channel.core.operation_triggers import Trigger

if TYPE_CHECKING:
    from synapse_channel.core.persistence import EventStore, StoredEvent

TERMINAL_OUTCOME_STATES = frozenset({"not_started", "confirmed_done", "confirmed_failed"})
"""Outcome states that end the executor's own attempt."""

OBSERVED_EFFECTS = frozenset({"observed", "absent"})
"""Verification effects that reconcile an ended attempt."""

STATE_NONE = "none"
STATE_OPEN = "open"
STATE_ENDED = "ended"
STATE_APPLIED = "applied"
STATE_SEALED = "sealed"
STATE_RECONCILED = "reconciled"
STATE_CLOSED = "closed"
ATTEMPT_STATES = (
    STATE_NONE,
    STATE_OPEN,
    STATE_ENDED,
    STATE_APPLIED,
    STATE_SEALED,
    STATE_RECONCILED,
    STATE_CLOSED,
)
"""Every attempt state reported over the wire."""

_RESTORED_KINDS = (
    EventKind.OPERATION_DECLARED,
    EventKind.OPERATION_OUTCOME,
    EventKind.OPERATION_VERIFIED,
    EventKind.OPERATION_ATTEMPT_ANSWERED,
    EventKind.OPERATION_SEALED,
    EventKind.OPERATION_STOPPED,
    EventKind.OPERATION_RESUMED,
    EventKind.OPERATION_STOP_READBACK,
    EventKind.OPERATION_LEVEL,
    EventKind.LEVEL_CRITERIA,
    EventKind.LEVEL_JUDGEMENT,
    EventKind.OPERATION_TRIGGER,
)


_STOP_KINDS = (
    EventKind.OPERATION_STOPPED,
    EventKind.OPERATION_RESUMED,
    EventKind.OPERATION_STOP_READBACK,
    EventKind.OPERATION_LEVEL,
    EventKind.LEVEL_CRITERIA,
    EventKind.LEVEL_JUDGEMENT,
    EventKind.OPERATION_TRIGGER,
)
"""The rows of a scope: stops, resumes, read-backs and levels, restored by ``_restore_stop``."""

_LEVEL_KINDS = (
    EventKind.OPERATION_LEVEL,
    EventKind.LEVEL_CRITERIA,
    EventKind.LEVEL_JUDGEMENT,
    EventKind.OPERATION_TRIGGER,
)
"""The rows of a scope's level: its records, criteria, judgements and triggers."""


@dataclass(frozen=True)
class OpenAttempt:
    """One attempt open in a scope, as a stop record lists it."""

    operation_id: str
    attempt_id: str
    task_id: str
    executor: str


@dataclass
class _Attempt:
    attempt_id: str
    executor: str
    approval_seq: int
    ended: bool = False
    applied: bool = False
    sealed: bool = False
    reconciled: bool = False
    marker_observed: bool = False
    verified_applied: bool = False
    verified_sealed: bool = False
    claimed_done: bool = False
    claimed_none: bool = False


@dataclass
class _Operation:
    fact: OperationFact
    attempts: list[_Attempt] = field(default_factory=list)
    closed: bool = False
    closed_seq: int = 0
    """The journal sequence of the independent verification that closed it; 0 while open."""
    contradicted_seq: int = 0
    """The sequence of the first contradiction recorded on the operation; 0 when none."""
    unknown_seqs: list[int] = field(default_factory=list)
    """The sequences of its executors' reports of an ``unknown`` outcome."""
    triggered: set[str] = field(default_factory=set)
    """The causes of the triggers the hub recorded for it, so none is written twice."""


@dataclass(frozen=True)
class AttemptSummary:
    """One attempt as ``operation_state`` reports it."""

    attempt_id: str
    executor: str
    state: str


@dataclass(frozen=True)
class AttemptView:
    """What the decision reads about one operation's attempts.

    Attributes
    ----------
    closed : bool
        An independent verification has verified the operation.
    attempt_open : bool
        An attempt is open.
    approval_consumed : bool
        The given approval has already admitted an attempt.
    unreconciled : bool
        An ended attempt has no independent observation since its end, or an
        earlier attempt's recomputed ``applied`` marker shows its effect landed.
    applied : bool, optional
        An earlier attempt's kept read-back recomputed to ``applied``.
    """

    closed: bool
    attempt_open: bool
    approval_consumed: bool
    unreconciled: bool
    applied: bool = False


def _deadline_of(intent: object) -> int | None:
    if not isinstance(intent, Mapping):
        return None
    deadline = intent.get("deadline")
    return deadline if isinstance(deadline, int) and not isinstance(deadline, bool) else None


def _time_of(payload: Mapping[str, Any]) -> float:
    """Return a row's ``hub_time``, or 0 when it states none."""
    value = payload.get("hub_time")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _mark_ended(attempt: _Attempt) -> None:
    """Mark ``attempt`` ended and reconcile it if its marker was already observed."""
    attempt.ended = True
    if attempt.marker_observed:
        attempt.reconciled = True


def _record_contradiction(entry: _Operation, seq: int) -> None:
    """Record the first contradiction on ``entry``; refuse a non-positive sequence."""
    if seq < 1:
        raise ValueError("a contradiction requires a positive journal sequence")
    if not entry.contradicted_seq:
        entry.contradicted_seq = seq


def _match_marker_attempt(
    entry: _Operation,
    operation_id: str,
    effect: str,
    evidence: Mapping[str, Any] | None,
) -> tuple[str, _Attempt | None, str | None]:
    """Match ``evidence`` against ``entry.attempts`` via :func:`match_verification_marker`."""
    match_kind, matched_id, blob_kind = match_verification_marker(
        operation_id,
        entry.fact.intent_sha256,
        [attempt.attempt_id for attempt in entry.attempts],
        effect,
        evidence,
    )
    matched = (
        None
        if matched_id is None
        else next((attempt for attempt in entry.attempts if attempt.attempt_id == matched_id), None)
    )
    return match_kind, matched, blob_kind


def _bound(entry: Mapping[str, Any]) -> int:
    """Return a criteria item's bound as a row states it; a malformed one reads 0."""
    bound = entry.get("max", entry.get("min", 0))
    return bound if type(bound) is int else 0


def _note_outcome_claims(
    entry: _Operation, last: _Attempt, state: str, external_effect: str, seq: int
) -> None:
    """Record executor claims and surface marker contradictions."""
    if "confirmed_done" in (state, external_effect):
        last.claimed_done = True
        if last.sealed:
            _record_contradiction(entry, seq)
    if "not_started" in (state, external_effect):
        last.claimed_none = True
        if last.applied:
            _record_contradiction(entry, seq)


class AttemptLedger:
    """The attempts of every declared operation, restored from ``store``.

    Parameters
    ----------
    store : EventStore or None
        The hub journal; ``None`` starts an empty ledger.
    """

    def __init__(self, store: EventStore | None) -> None:
        self._operations: dict[str, _Operation] = {}
        self._stop_ledger = StopLedger()
        self._level_ledger = LevelLedger()
        if store is not None:
            for event in store.iter_events(kinds=_RESTORED_KINDS):
                self.apply_event(event)

    @property
    def operations(self) -> dict[str, _Operation]:
        """Return the live table, for :meth:`publish_from` of the hub's actor."""
        return self._operations

    @property
    def stops(self) -> dict[str, _ScopeStops]:
        """Return the live stops of every scope, for :meth:`publish_from`."""
        return self._stop_ledger.stops

    @property
    def stop_rows(self) -> dict[int, _StopRow]:
        """Return the live recorded stops by journal sequence, for :meth:`publish_from`."""
        return self._stop_ledger.stop_rows

    @property
    def levels(self) -> dict[str, _ScopeLevel]:
        """Return the live levels of every scope, for :meth:`publish_from`."""
        return self._level_ledger.levels

    @property
    def criteria(self) -> dict[str, LevelCriteria]:
        """Return the recorded criteria by digest, for :meth:`publish_from`."""
        return self._level_ledger.criteria

    @property
    def judgements(self) -> dict[int, LevelJudgement]:
        """Return the recorded judgements by sequence, for :meth:`publish_from`."""
        return self._level_ledger.judgements

    def publish_from(self, other: AttemptLedger) -> None:
        """Install the tables of a candidate that the hub's actor has made durable."""
        self._operations = other.operations
        self._stop_ledger.publish_from(other.stops, other.stop_rows)
        self._level_ledger.publish_from(other.levels, other.criteria, other.judgements)

    def apply_event(self, event: StoredEvent) -> None:
        """Apply one journalled row; a row that lacks a field the hub writes is skipped."""
        payload = event.payload
        if event.kind in _STOP_KINDS:
            self._restore_stop(event.kind, event.seq, payload)
            return
        operation_id, sender = payload.get("operation_id"), payload.get("sender")
        if not isinstance(operation_id, str) or not isinstance(sender, str):
            return
        self._restore_operation(event.kind, event.seq, operation_id, sender, payload)

    def _restore_operation(
        self, kind: str, seq: int, operation_id: str, sender: str, payload: Mapping[str, Any]
    ) -> None:
        if kind == EventKind.OPERATION_DECLARED:
            self._restore_declaration(operation_id, sender, payload)
        elif kind == EventKind.OPERATION_OUTCOME:
            self.note_outcome(
                operation_id,
                sender,
                str(payload.get("state") or ""),
                external_effect=str(payload.get("external_effect") or ""),
                seq=seq,
            )
        elif kind == EventKind.OPERATION_VERIFIED:
            effect, result = str(payload.get("effect") or ""), str(payload.get("result") or "")
            raw_evidence = payload.get("evidence")
            evidence = raw_evidence if isinstance(raw_evidence, Mapping) else None
            self.note_verification(
                operation_id, sender, effect=effect, result=result, seq=seq, evidence=evidence
            )
        elif (
            kind == EventKind.OPERATION_ATTEMPT_ANSWERED and payload.get("answer") == APPROVED_FRESH
        ):
            attempt, approval = payload.get("attempt_id"), payload.get("approval_seq")
            if isinstance(attempt, str) and isinstance(approval, int):
                self.open_attempt(operation_id, attempt, executor=sender, approval_seq=approval)
        elif kind == EventKind.OPERATION_SEALED:
            self.note_seal(operation_id, str(payload.get("attempt_id") or ""), seq=seq)

    def _restore_declaration(
        self, operation_id: str, sender: str, payload: Mapping[str, Any]
    ) -> None:
        task_id, declared_at = payload.get("task_id"), payload.get("recorded_at")
        if not isinstance(task_id, str) or not isinstance(declared_at, (int, float)):
            return
        digest, effect, intent = (
            payload.get("intent_sha256"),
            payload.get("effect"),
            payload.get("intent"),
        )
        assurance = intent.get("required_assurance") if isinstance(intent, Mapping) else None
        self.note_declaration(
            operation_id,
            OperationFact(
                task_id=task_id,
                declarer=sender,
                intent_sha256=digest if isinstance(digest, str) else None,
                deadline=_deadline_of(intent),
                declared_at=float(declared_at),
                effect=effect if isinstance(effect, str) else None,
                assurance=assurance if isinstance(assurance, str) else None,
            ),
        )

    def _restore_stop(self, kind: str, seq: int, payload: Mapping[str, Any]) -> None:
        if kind in _LEVEL_KINDS:
            self._restore_level(kind, seq, payload)
            return
        if kind == EventKind.OPERATION_STOP_READBACK:
            self._restore_readback(seq, payload)
            return
        scope = payload.get("scope")
        if not isinstance(scope, str):
            return
        if kind == EventKind.OPERATION_RESUMED:
            self.note_resume(scope, seq)
            return
        epoch, listed = payload.get("epoch"), payload.get("listed")
        pairs = (
            tuple(
                (str(item["operation_id"]), str(item["attempt_id"]))
                for item in listed
                if isinstance(item, Mapping)
                and isinstance(item.get("operation_id"), str)
                and isinstance(item.get("attempt_id"), str)
            )
            if isinstance(listed, list)
            else ()
        )
        epoch = epoch if type(epoch) is int else 0
        self.note_stop(scope, seq, epoch=epoch, listed=pairs, recorded_at=_time_of(payload))

    def _restore_readback(self, seq: int, payload: Mapping[str, Any]) -> None:
        stop_seq, attempt, result = (
            payload.get("stop_seq"),
            payload.get("attempt_id"),
            payload.get("result"),
        )
        if type(stop_seq) is int and isinstance(attempt, str) and isinstance(result, str):
            self.note_readback(
                stop_seq,
                attempt,
                result,
                seq=seq,
                operation_id=str(payload.get("operation_id") or ""),
                marker_ref=str(payload.get("marker_ref") or ""),
                object_format=str(payload.get("object_format") or ""),
                marker_object=str(payload.get("marker_object") or ""),
            )

    def note_stop(
        self,
        scope: str,
        seq: int,
        *,
        epoch: int,
        listed: tuple[tuple[str, str], ...],
        recorded_at: float = 0.0,
    ) -> None:
        """Record that ``scope`` is stopped by the row at ``seq``, listing (operation, attempt)."""
        self._stop_ledger.note_stop(scope, seq, epoch=epoch, listed=listed, recorded_at=recorded_at)

    def stop_lists(self, stop_seq: int, operation_id: str, attempt_id: str) -> bool:
        """Return whether the stop recorded at ``stop_seq`` listed this attempt."""
        return self._stop_ledger.lists(stop_seq, operation_id, attempt_id)

    def note_readback(
        self,
        stop_seq: int,
        attempt_id: str,
        result: str,
        *,
        seq: int = 0,
        operation_id: str = "",
        marker_ref: str = "",
        object_format: str = "",
        marker_object: str = "",
    ) -> None:
        """Record a kept read-back without overwriting a closed or unsealable one.

        An ``unread`` read-back may be superseded only by a closing result.
        When the marker fields recompute to ``sealed``, the attempt is marked
        sealed, and an earlier ``confirmed_done`` claim or matched ``applied``
        marker verification on it records a contradiction. When they recompute
        to ``applied``, the attempt ends as ``applied`` (not sealed), and an
        earlier ``not_started`` claim or matched ``sealed`` marker verification
        on it records a contradiction.
        """
        self._stop_ledger.record_readback(stop_seq, attempt_id, result, seq=seq)
        entry = self._operations.get(operation_id)
        if entry is None or not self.stop_lists(stop_seq, operation_id, attempt_id):
            return
        if marker_reads_sealed(
            operation_id=operation_id,
            attempt_id=attempt_id,
            intent_sha256=entry.fact.intent_sha256,
            marker_ref=marker_ref,
            object_format=object_format,
            marker_object=marker_object,
        ):
            self._mark_attempt_from_readback(entry, attempt_id, kind=RESULT_SEALED, seq=seq)
        elif marker_reads_applied(
            operation_id=operation_id,
            attempt_id=attempt_id,
            intent_sha256=entry.fact.intent_sha256,
            marker_ref=marker_ref,
            object_format=object_format,
            marker_object=marker_object,
        ):
            self._mark_attempt_from_readback(entry, attempt_id, kind=RESULT_APPLIED, seq=seq)

    def _mark_attempt_from_readback(
        self, entry: _Operation, attempt_id: str, *, kind: str, seq: int
    ) -> None:
        for attempt in entry.attempts:
            if attempt.attempt_id != attempt_id:
                continue
            if kind == RESULT_SEALED and not attempt.applied:
                attempt.sealed = True
                _mark_ended(attempt)
                if attempt.claimed_done or attempt.verified_applied:
                    _record_contradiction(entry, seq)
            elif kind == RESULT_APPLIED and not attempt.sealed:
                attempt.applied = True
                _mark_ended(attempt)
                if attempt.claimed_none or attempt.verified_sealed:
                    _record_contradiction(entry, seq)

    def readback_of(self, stop_seq: int, attempt_id: str) -> str | None:
        """Return the kept read-back result of an attempt for a stop, or ``None``."""
        return self._stop_ledger.readback_of(stop_seq, attempt_id)

    def kept_readback(self, stop_seq: int, attempt_id: str) -> tuple[str, int] | None:
        """Return ``(result, seq)`` of the kept read-back for ``(stop_seq, attempt_id)``."""
        return self._stop_ledger.kept_readback(stop_seq, attempt_id)

    def note_level(self, scope: str, seq: int, *, level: str, epoch: int) -> None:
        """Record that the row at ``seq`` set ``scope`` to ``level`` at ``epoch``."""
        self._level_ledger.note_level(scope, seq, level=level, epoch=epoch)

    def level_of(self, scope: str) -> tuple[str, int]:
        """Return the level of ``scope`` and its latest record's epoch; ``A1`` and 0 without one."""
        return self._level_ledger.level_of(scope)

    def level_facts(self, operation_id: str) -> tuple[str, tuple[str, int] | None]:
        """Return an operation's level, the lowest of its scopes, and its level line.

        Returns
        -------
        tuple
            The lowest level among the scopes that contain the operation, and
            the scope and sequence of the latest level record of any of them,
            or ``None`` when none has a record.
        """
        scopes = self.operation_scopes(operation_id)
        line: tuple[str, int] | None = None
        for scope in scopes:
            record = self.levels.get(scope)
            if record is not None and (line is None or record.line > line[1]):
                line = (scope, record.line)
        return lowest(self.level_of(scope)[0] for scope in scopes), line

    def scopes_containing(self, scope: str) -> tuple[str, ...]:
        """Return ``scope`` and every scope that contains it, narrowest first."""
        if scope == "installation":
            return (scope,)
        kind, _, name = scope.partition(":")
        if kind == "operation" and self.operation(name) is not None:
            return self.operation_scopes(name)
        return (scope, "installation")

    def stopped_within(self, scope: str) -> tuple[str, int] | None:
        """Return the narrowest standing stop of ``scope`` or of a scope containing it."""
        for container in self.scopes_containing(scope):
            standing = self.standing_stop(container)
            if standing is not None:
                return container, standing[0]
        return None

    def _restore_level(self, kind: str, seq: int, payload: Mapping[str, Any]) -> None:
        if kind == EventKind.OPERATION_TRIGGER:
            self.note_trigger(str(payload.get("cause") or ""), payload)
        elif kind == EventKind.OPERATION_LEVEL:
            scope, level, epoch = payload.get("scope"), payload.get("level"), payload.get("epoch")
            if isinstance(scope, str) and level in LEVELS and type(epoch) is int:
                self.note_level(scope, seq, level=str(level), epoch=epoch)
        else:
            self._restore_ladder(kind, seq, payload)

    def _restore_ladder(self, kind: str, seq: int, payload: Mapping[str, Any]) -> None:
        digest = payload.get("digest")
        if not isinstance(digest, str):
            return
        if kind == EventKind.LEVEL_JUDGEMENT:
            verdict, judge = payload.get("verdict"), payload.get("judge")
            if isinstance(verdict, str) and isinstance(judge, str):
                self.note_judgement(seq, LevelJudgement(digest, verdict, judge))
            return
        scope, target, raw = payload.get("scope"), payload.get("target"), payload.get("items")
        declared_by = payload.get("declared_by")
        if not (isinstance(scope, str) and isinstance(target, str) and isinstance(raw, list)):
            return
        items = tuple(
            CriteriaItem(str(entry["item"]), _bound(entry), "max" in entry)
            for entry in raw
            if isinstance(entry, Mapping) and isinstance(entry.get("item"), str)
        )
        self.note_criteria(digest, LevelCriteria(seq, scope, target, items, str(declared_by or "")))

    def note_criteria(self, digest: str, criteria: LevelCriteria) -> None:
        """Record a criteria under its digest; the first record of a digest stands."""
        self._level_ledger.note_criteria(digest, criteria)

    def note_judgement(self, seq: int, judgement: LevelJudgement) -> None:
        """Record the judgement journalled at ``seq``."""
        self._level_ledger.note_judgement(seq, judgement)

    def window_start(self, scope: str, criteria_seq: int) -> int:
        """Return where a count of ``scope`` starts: after its criteria and its latest step down."""
        return self._level_ledger.window_start(scope, criteria_seq)

    def _in_scope(self, scope: str) -> list[_Operation]:
        return [
            entry
            for operation_id, entry in self._operations.items()
            if scope in self.operation_scopes(operation_id)
        ]

    def level_counts(self, scope: str, since: int, now: float) -> dict[str, int]:
        """Count every criteria item in ``scope`` from the facts after ``since``.

        Nothing is stored: each call reads the ledger as it stands. A report
        or verification by an operation's declarer never counts.
        """
        counts: dict[str, int] = {
            ITEM_INCORRECT_ACCEPTANCES: 0,
            ITEM_UNKNOWN_OUTCOMES: 0,
            ITEM_STOPS_AT_REST: 0,
        }
        for entry in self._in_scope(scope):
            if entry.closed_seq > since:
                key = f"{ITEM_VERIFIED_EFFECTS}:{entry.fact.effect}"
                counts[key] = counts.get(key, 0) + 1
            if entry.contradicted_seq > since:
                counts[ITEM_INCORRECT_ACCEPTANCES] += 1
            counts[ITEM_UNKNOWN_OUTCOMES] += sum(seq > since for seq in entry.unknown_seqs)
            deadline = entry.fact.deadline
            if deadline is not None and deadline <= now and not entry.closed:
                counts[ITEM_UNKNOWN_OUTCOMES] += sum(
                    attempt.approval_seq > since for attempt in entry.attempts
                )
        containing = self.scopes_containing(scope)
        for seq, row in self.stop_rows.items():
            computed = self.stop_state(seq) if seq > since and row.scope in containing else None
            if computed is not None and computed.state == STATE_AT_REST:
                counts[ITEM_STOPS_AT_REST] += 1
        return counts

    def level_actors(self, scope: str, since: int) -> frozenset[str]:
        """Return the actors of a count: the declarers and executors of what it counts."""
        actors: set[str] = set()
        for entry in self._in_scope(scope):
            active = (
                entry.closed_seq > since
                or entry.contradicted_seq > since
                or any(seq > since for seq in entry.unknown_seqs)
                or any(attempt.approval_seq > since for attempt in entry.attempts)
            )
            if active:
                actors.add(entry.fact.declarer)
                actors.update(attempt.executor for attempt in entry.attempts)
        return frozenset(actors)

    def note_trigger(self, cause: str, subject: Mapping[str, Any]) -> None:
        """Record that the hub wrote the trigger ``cause`` for an operation or a stop."""
        operation_id, stop_seq = subject.get("operation_id"), subject.get("stop_seq")
        entry = self._operations.get(operation_id) if isinstance(operation_id, str) else None
        if entry is not None:
            entry.triggered.add(cause)
        self._stop_ledger.mark_bound_triggered(stop_seq)

    def contradicted_at(self, operation_id: str) -> int:
        """Return the sequence of the operation's first independent contradiction, or 0."""
        entry = self._operations.get(operation_id)
        return 0 if entry is None else entry.contradicted_seq

    def operation_trigger(self, operation_id: str, cause: str, *, shown_by: int = 0) -> Trigger:
        """Return the trigger ``cause`` of an operation: every containing scope above A1 falls."""
        return _operation_trigger(self, operation_id, cause, shown_by=shown_by)

    def due_triggers(self, now: float, stop_bound: float, since: float = 0.0) -> list[Trigger]:
        """Return the triggers the watcher records at ``now``, once each.

        An operation past its intent's deadline with an ended attempt and no
        independent verification is ``deadline_unverified``; a stop still
        ``stop requested`` ``stop_bound`` seconds after it was recorded, and not
        lifted, is ``stop_unsettled``. Only a deadline or a bound that passed
        after ``since`` counts, so a watcher started on a long journal writes no
        burst of old triggers.
        """
        return _due_triggers(self, now, stop_bound, since)

    def actor_trigger(self, scope: str, target: str, actor: str) -> Trigger:
        """Return an actor's own step down of ``scope`` to ``target``."""
        return _actor_trigger(self, scope, target, actor)

    def scope_actors(self, scope: str) -> frozenset[str]:
        """Return the declarers and executors of every operation ``scope`` contains."""
        found: set[str] = set()
        for entry in self._in_scope(scope):
            found.add(entry.fact.declarer)
            found.update(attempt.executor for attempt in entry.attempts)
        return frozenset(found)

    def automated_floor(self, digest: str) -> str | None:
        """Return the narrowest A1 scope of an operation declared with intent ``digest``.

        Operations are read in id order; ``None`` when none that declares the
        digest is at A1, or none declares it.
        """
        for operation_id in sorted(self._operations):
            if self._operations[operation_id].fact.intent_sha256 != digest:
                continue
            for scope in self.operation_scopes(operation_id):
                if self.level_of(scope)[0] == DEFAULT_LEVEL:
                    return scope
        return None

    def stop_identity(self, stop_seq: int) -> tuple[str, int] | None:
        """Return the scope and epoch of the stop recorded at ``stop_seq``, or ``None``."""
        return self._stop_ledger.stop_identity(stop_seq)

    def stops_listing(self, operation_id: str) -> tuple[int, ...]:
        """Return the sequences of the recorded stops that listed an attempt of the operation."""
        return self._stop_ledger.stops_listing(operation_id)

    def attempt_count(self, operation_id: str) -> int:
        """Return the number of attempts opened for ``operation_id``."""
        entry = self._operations.get(operation_id)
        return 0 if entry is None else len(entry.attempts)

    def listed_pair_count(self, operation_id: str) -> int:
        """Return how many recorded stop listings name an attempt of ``operation_id``."""
        return self._stop_ledger.listed_pair_count(operation_id)

    def reconciled(self, operation_id: str, attempt_id: str) -> bool:
        """Return whether the attempt is reconciled or its operation is closed."""
        entry = self._operations.get(operation_id)
        if entry is None:
            return False
        attempt = next((item for item in entry.attempts if item.attempt_id == attempt_id), None)
        return entry.closed or (attempt is not None and attempt.reconciled)

    def assurance(self, operation_id: str) -> str | None:
        """Return the operation's declared assurance, or ``None`` when it has none."""
        fact = self.operation(operation_id)
        return None if fact is None else fact.assurance

    def stop_state(self, stop_seq: int) -> StopState | None:
        """Return the computed state of the stop recorded at ``stop_seq``, or ``None``.

        The state reads only the kept read-backs and the attempts' reconciliation;
        an executor's report is never read. The profile is ``cooperative``: the
        hub keeps no evidence of the isolated profile. A resumed stop keeps the
        state computed at its resume.
        """
        return self._stop_ledger.state(stop_seq, self)

    def stop_closed(self, stop_seq: int) -> bool:
        """Return whether every attempt the stop listed has a ``sealed`` or ``applied`` read-back.

        An executor's own report is not read: a stop trusts markers, not reports.
        A stop that listed nothing is closed; an unknown sequence is not.
        """
        return self._stop_ledger.stop_closed(stop_seq)

    def note_resume(self, scope: str, seq: int) -> None:
        """Record that the row at ``seq`` lifted the stop of ``scope``; its state stands as now."""
        self._stop_ledger.resume(scope, seq, self)

    def standing_stop(self, scope: str) -> tuple[int, int] | None:
        """Return the standing stop of ``scope`` as (journal sequence, epoch), or ``None``."""
        return self._stop_ledger.standing_stop(scope)

    def epoch(self, scope: str) -> int:
        """Return the epoch of the latest stop of ``scope``; 0 when it was never stopped."""
        return self._stop_ledger.epoch(scope)

    def operation_scopes(self, operation_id: str) -> tuple[str, ...]:
        """Return every scope that contains ``operation_id``, the narrowest first."""
        fact = self.operation(operation_id)
        if fact is None:
            return scopes_of(operation_id, None, None)
        return scopes_of(operation_id, fact.declarer, fact.effect)

    def stop_facts(
        self, operation_id: str
    ) -> tuple[tuple[str, int] | None, tuple[str, int] | None]:
        """Return what a stop means for ``operation_id``.

        Returns
        -------
        tuple
            The narrowest stopped scope containing it with its stop's sequence,
            or ``None``; and the scope and sequence of the latest stop or resume
            of any scope containing it, or ``None``.
        """
        stopped_by: tuple[str, int] | None = None
        line: tuple[str, int] | None = None
        for scope in self.operation_scopes(operation_id):
            stops = self.stops.get(scope)
            if stops is None:
                continue
            if stopped_by is None and stops.standing is not None:
                stopped_by = (scope, stops.standing)
            if stops.line and (line is None or stops.line > line[1]):
                line = (scope, stops.line)
        return stopped_by, line

    def stop_of_intent(self, digest: str) -> tuple[str, int] | None:
        """Return a standing stop that covers an operation declared with intent ``digest``.

        When several operations declare the digest, the first one in operation-id
        order that is stopped gives its narrowest stopped scope and that stop's
        sequence. ``None`` when no such operation is stopped or none declares it.
        """
        for operation_id in sorted(self._operations):
            if self._operations[operation_id].fact.intent_sha256 != digest:
                continue
            stopped_by, _line = self.stop_facts(operation_id)
            if stopped_by is not None:
                return stopped_by
        return None

    def standing_listed(self, stop_seq: int) -> tuple[OpenAttempt, ...]:
        """Return the attempts ``stop_seq`` listed that are neither sealed nor reconciled."""
        found: list[OpenAttempt] = []
        for operation_id, attempt_id in self._stop_ledger.listed(stop_seq):
            entry = self._operations.get(operation_id)
            if entry is None or entry.closed:
                continue
            attempt = next((item for item in entry.attempts if item.attempt_id == attempt_id), None)
            if attempt is not None and not (attempt.sealed or attempt.reconciled):
                found.append(
                    OpenAttempt(
                        operation_id, attempt.attempt_id, entry.fact.task_id, attempt.executor
                    )
                )
        return tuple(found)

    def open_in(self, scope: str) -> tuple[OpenAttempt, ...]:
        """Return every attempt in ``scope`` that is neither sealed nor reconciled."""
        found: list[OpenAttempt] = []
        for operation_id, entry in self._operations.items():
            if entry.closed or scope not in self.operation_scopes(operation_id):
                continue
            found.extend(
                OpenAttempt(operation_id, attempt.attempt_id, entry.fact.task_id, attempt.executor)
                for attempt in entry.attempts
                if not (attempt.sealed or attempt.reconciled)
            )
        return tuple(found)

    def actors(self, scope: str) -> frozenset[str]:
        """Return the actors of ``scope``: declarers of operations it lists or would list."""
        stops = self.stops.get(scope)
        listed = () if stops is None else stops.listed
        declarers = {
            entry.fact.declarer
            for operation_id, entry in self._operations.items()
            if operation_id in listed
        }
        declarers.update(
            self._operations[attempt.operation_id].fact.declarer for attempt in self.open_in(scope)
        )
        return frozenset(declarers)

    def note_declaration(self, operation_id: str, fact: OperationFact) -> None:
        """Record a declaration; the first declaration of an id stands."""
        self._operations.setdefault(operation_id, _Operation(fact))

    def note_outcome(
        self,
        operation_id: str,
        sender: str,
        state: str,
        *,
        external_effect: str = "",
        seq: int = 0,
    ) -> None:
        """End the open attempt when its own executor reports a terminal state.

        An executor's report of an ``unknown`` outcome ends nothing; its
        sequence is kept, as the level criteria count unknown outcomes. An
        executor claiming ``confirmed_done`` on a sealed attempt, or
        ``not_started`` on an applied attempt, records a contradiction.
        """
        entry = self._operations.get(operation_id)
        if entry is None:
            return
        executors = {attempt.executor for attempt in entry.attempts}
        if state == "unknown" and sender in executors:
            entry.unknown_seqs.append(seq)
        last = next(
            (attempt for attempt in reversed(entry.attempts) if attempt.executor == sender),
            None,
        )
        if last is not None:
            _note_outcome_claims(entry, last, state, external_effect, seq)
        if state not in TERMINAL_OUTCOME_STATES:
            return
        for attempt in entry.attempts:
            if not attempt.ended and attempt.executor == sender:
                _mark_ended(attempt)

    def note_verification(
        self,
        operation_id: str,
        sender: str,
        *,
        effect: str,
        result: str,
        seq: int = 0,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        """Reconcile ended or marker-matched attempts, and close on verified.

        When ``effect`` is ``observed`` or ``absent`` and ``sender`` is not the
        declarer, ``evidence`` is classified as ``unscoped`` (no non-empty
        ``marker_object``: reconciles currently ended attempts only),
        ``matched`` (recomputed ``marker_object`` for one attempt: marks that
        attempt marker-observed and reconciles it on or after its end without
        affecting other attempts), or ``mismatched`` (reconciles nothing).

        The sequence of the closing verification is kept, and so is the first
        later ``not_verified`` by an identity other than the declarer or any
        matched marker-blob disagreement with a ``sealed`` or ``applied``
        record on the same attempt (each requiring ``seq >= 1``).
        """
        entry = self._operations.get(operation_id)
        if entry is None or sender == entry.fact.declarer:
            return
        if result == "verified" and not entry.closed:
            entry.closed, entry.closed_seq = True, seq
        elif result == "not_verified" and entry.closed:
            _record_contradiction(entry, seq)
        if effect in OBSERVED_EFFECTS:
            match_kind, matched, blob_kind = _match_marker_attempt(
                entry, operation_id, effect, evidence
            )
            if matched is not None:
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

    def note_seal(self, operation_id: str, attempt_id: str, *, seq: int) -> None:
        """End the open attempt ``attempt_id`` as sealed; an ended one is left as it is.

        Raises :class:`ValueError` when ``seq < 1`` so a contradiction can never
        be lost silently to a falsy sequence.
        """
        if seq < 1:
            raise ValueError("note_seal requires a positive journal sequence")
        entry = self._operations.get(operation_id)
        if entry is None:
            return
        for attempt in entry.attempts:
            if attempt.attempt_id == attempt_id and not attempt.ended:
                attempt.sealed = True
                _mark_ended(attempt)
                if attempt.claimed_done or attempt.verified_applied:
                    _record_contradiction(entry, seq)

    def open_attempt(
        self, operation_id: str, attempt_id: str, *, executor: str, approval_seq: int
    ) -> None:
        """Open an attempt that consumes ``approval_seq``."""
        entry = self._operations.get(operation_id)
        if entry is not None:
            entry.attempts.append(_Attempt(attempt_id, executor, approval_seq))

    def operation(self, operation_id: str) -> OperationFact | None:
        """Return the declared operation, or ``None``."""
        entry = self._operations.get(operation_id)
        return None if entry is None else entry.fact

    def view(self, operation_id: str, approval_seq: int | None) -> AttemptView:
        """Return what the decision reads about ``operation_id``'s attempts."""
        entry = self._operations.get(operation_id)
        if entry is None:
            return AttemptView(False, False, False, False)
        applied = any(attempt.applied for attempt in entry.attempts)
        return AttemptView(
            closed=entry.closed,
            attempt_open=any(not attempt.ended for attempt in entry.attempts),
            approval_consumed=any(
                attempt.approval_seq == approval_seq for attempt in entry.attempts
            ),
            unreconciled=applied
            or any(attempt.ended and not attempt.reconciled for attempt in entry.attempts),
            applied=applied,
        )

    def summary(self, operation_id: str) -> tuple[bool, tuple[AttemptSummary, ...]] | None:
        """Return whether the operation is closed and each attempt's state, or ``None``."""
        entry = self._operations.get(operation_id)
        if entry is None:
            return None
        attempts = tuple(
            AttemptSummary(
                item.attempt_id, item.executor, self.attempt_state(operation_id, item.attempt_id)
            )
            for item in entry.attempts
        )
        return entry.closed, attempts

    def attempt_state(self, operation_id: str, attempt_id: str) -> str:
        """Return one of :data:`ATTEMPT_STATES` for ``attempt_id``."""
        entry = self._operations.get(operation_id)
        found = (
            None
            if entry is None
            else next((item for item in entry.attempts if item.attempt_id == attempt_id), None)
        )
        if entry is None or found is None:
            return STATE_NONE
        if entry.closed:
            return STATE_CLOSED
        if found.reconciled:
            return STATE_RECONCILED
        if found.sealed:
            return STATE_SEALED
        if found.applied:
            return STATE_APPLIED
        return STATE_ENDED if found.ended else STATE_OPEN
