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
* after the end, a verification by a sender other than the declarer with the
  effect ``observed`` or ``absent`` reconciles it;
* a verification by a sender other than the declarer with the result
  ``verified`` closes the operation.

A verification by the declarer is kept by the journal and counts for nothing
here. Restoring checks no time.

The ledger also keeps each scope's stops (:mod:`~synapse_channel.core.operation_stops`):

* an ``operation_stopped`` row stops its scope, at its epoch, and names the
  operations whose attempts were open in it;
* an ``operation_resumed`` row lifts the stop of its scope;
* the journal sequence of a scope's latest stop or resume is its line;
* an ``operation_stop_readback`` row records what a stop read back for one
  attempt it listed. Only a ``sealed`` or ``applied`` read-back closes the
  attempt for that stop; an executor's report never does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempts import APPROVED_FRESH, OperationFact
from synapse_channel.core.operation_stop_readbacks import CLOSING_RESULTS
from synapse_channel.core.operation_stops import scopes_of

if TYPE_CHECKING:
    from synapse_channel.core.persistence import EventStore, StoredEvent

TERMINAL_OUTCOME_STATES = frozenset({"not_started", "confirmed_done", "confirmed_failed"})
"""Outcome states that end the executor's own attempt."""

OBSERVED_EFFECTS = frozenset({"observed", "absent"})
"""Verification effects that reconcile an ended attempt."""

STATE_NONE = "none"
STATE_OPEN = "open"
STATE_ENDED = "ended"
STATE_SEALED = "sealed"
STATE_RECONCILED = "reconciled"
STATE_CLOSED = "closed"

_RESTORED_KINDS = (
    EventKind.OPERATION_DECLARED,
    EventKind.OPERATION_OUTCOME,
    EventKind.OPERATION_VERIFIED,
    EventKind.OPERATION_ATTEMPT_ANSWERED,
    EventKind.OPERATION_SEALED,
    EventKind.OPERATION_STOPPED,
    EventKind.OPERATION_RESUMED,
    EventKind.OPERATION_STOP_READBACK,
)


_STOP_KINDS = (
    EventKind.OPERATION_STOPPED,
    EventKind.OPERATION_RESUMED,
    EventKind.OPERATION_STOP_READBACK,
)
"""The rows of stops, resumes and read-backs, restored by :meth:`AttemptLedger._restore_stop`."""


@dataclass
class _ScopeStops:
    """One scope's stops: the standing stop, its epoch, its listed operations and the line."""

    standing: int | None = None
    epoch: int = 0
    listed: tuple[str, ...] = ()
    line: int = 0


@dataclass
class _StopRow:
    """One recorded stop: its scope, the attempts it listed and what was read back for each."""

    scope: str
    listed: tuple[tuple[str, str], ...]
    readbacks: dict[str, str]


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
    sealed: bool = False
    reconciled: bool = False


@dataclass
class _Operation:
    fact: OperationFact
    attempts: list[_Attempt] = field(default_factory=list)
    closed: bool = False


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
        An ended attempt has no independent observation since its end.
    """

    closed: bool
    attempt_open: bool
    approval_consumed: bool
    unreconciled: bool


def _deadline_of(intent: object) -> int | None:
    if not isinstance(intent, Mapping):
        return None
    deadline = intent.get("deadline")
    return deadline if isinstance(deadline, int) and not isinstance(deadline, bool) else None


class AttemptLedger:
    """The attempts of every declared operation, restored from ``store``.

    Parameters
    ----------
    store : EventStore or None
        The hub journal; ``None`` starts an empty ledger.
    """

    def __init__(self, store: EventStore | None) -> None:
        self._operations: dict[str, _Operation] = {}
        self._stops: dict[str, _ScopeStops] = {}
        self._stop_rows: dict[int, _StopRow] = {}
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
        return self._stops

    @property
    def stop_rows(self) -> dict[int, _StopRow]:
        """Return the live recorded stops by journal sequence, for :meth:`publish_from`."""
        return self._stop_rows

    def publish_from(self, other: AttemptLedger) -> None:
        """Install the tables of a candidate that the hub's actor has made durable."""
        self._operations = other.operations
        self._stops = other.stops
        self._stop_rows = other.stop_rows

    def apply_event(self, event: StoredEvent) -> None:
        """Apply one journalled row; a row that lacks a field the hub writes is skipped."""
        payload = event.payload
        if event.kind in _STOP_KINDS:
            self._restore_stop(event.kind, event.seq, payload)
            return
        operation_id, sender = payload.get("operation_id"), payload.get("sender")
        if not isinstance(operation_id, str) or not isinstance(sender, str):
            return
        if event.kind == EventKind.OPERATION_DECLARED:
            self._restore_declaration(operation_id, sender, payload)
        elif event.kind == EventKind.OPERATION_OUTCOME:
            self.note_outcome(operation_id, sender, str(payload.get("state") or ""))
        elif event.kind == EventKind.OPERATION_VERIFIED:
            effect, result = str(payload.get("effect") or ""), str(payload.get("result") or "")
            self.note_verification(operation_id, sender, effect=effect, result=result)
        elif (
            event.kind == EventKind.OPERATION_ATTEMPT_ANSWERED
            and payload.get("answer") == APPROVED_FRESH
        ):
            attempt, approval = payload.get("attempt_id"), payload.get("approval_seq")
            if isinstance(attempt, str) and isinstance(approval, int):
                self.open_attempt(operation_id, attempt, executor=sender, approval_seq=approval)
        elif event.kind == EventKind.OPERATION_SEALED:
            self.note_seal(operation_id, str(payload.get("attempt_id") or ""))

    def _restore_declaration(
        self, operation_id: str, sender: str, payload: Mapping[str, Any]
    ) -> None:
        task_id, declared_at = payload.get("task_id"), payload.get("recorded_at")
        if not isinstance(task_id, str) or not isinstance(declared_at, (int, float)):
            return
        digest, effect = payload.get("intent_sha256"), payload.get("effect")
        self.note_declaration(
            operation_id,
            OperationFact(
                task_id=task_id,
                declarer=sender,
                intent_sha256=digest if isinstance(digest, str) else None,
                deadline=_deadline_of(payload.get("intent")),
                declared_at=float(declared_at),
                effect=effect if isinstance(effect, str) else None,
            ),
        )

    def _restore_stop(self, kind: str, seq: int, payload: Mapping[str, Any]) -> None:
        if kind == EventKind.OPERATION_STOP_READBACK:
            stop_seq, attempt, result = (
                payload.get("stop_seq"),
                payload.get("attempt_id"),
                payload.get("result"),
            )
            if type(stop_seq) is int and isinstance(attempt, str) and isinstance(result, str):
                self.note_readback(stop_seq, attempt, result)
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
        self.note_stop(scope, seq, epoch=epoch if type(epoch) is int else 0, listed=pairs)

    def note_stop(
        self, scope: str, seq: int, *, epoch: int, listed: tuple[tuple[str, str], ...]
    ) -> None:
        """Record that ``scope`` is stopped by the row at ``seq``, listing (operation, attempt)."""
        stops = self._stops.setdefault(scope, _ScopeStops())
        names = tuple(dict.fromkeys(operation_id for operation_id, _attempt in listed))
        stops.standing, stops.epoch, stops.listed, stops.line = seq, epoch, names, seq
        self._stop_rows[seq] = _StopRow(scope, listed, {})

    def stop_lists(self, stop_seq: int, operation_id: str, attempt_id: str) -> bool:
        """Return whether the stop recorded at ``stop_seq`` listed this attempt."""
        row = self._stop_rows.get(stop_seq)
        return row is not None and (operation_id, attempt_id) in row.listed

    def note_readback(self, stop_seq: int, attempt_id: str, result: str) -> None:
        """Record a kept read-back of ``attempt_id`` for the stop at ``stop_seq``."""
        row = self._stop_rows.get(stop_seq)
        if row is not None:
            row.readbacks[attempt_id] = result

    def readback_of(self, stop_seq: int, attempt_id: str) -> str | None:
        """Return the latest kept read-back of an attempt for a stop, or ``None``."""
        row = self._stop_rows.get(stop_seq)
        return None if row is None else row.readbacks.get(attempt_id)

    def stop_closed(self, stop_seq: int) -> bool:
        """Return whether every attempt the stop listed has a ``sealed`` or ``applied`` read-back.

        An executor's own report is not read: a stop trusts markers, not reports.
        A stop that listed nothing is closed; an unknown sequence is not.
        """
        row = self._stop_rows.get(stop_seq)
        if row is None:
            return False
        return all(row.readbacks.get(attempt) in CLOSING_RESULTS for _op, attempt in row.listed)

    def note_resume(self, scope: str, seq: int) -> None:
        """Record that the row at ``seq`` lifted the stop of ``scope``."""
        stops = self._stops.setdefault(scope, _ScopeStops())
        stops.standing, stops.line = None, seq

    def standing_stop(self, scope: str) -> tuple[int, int] | None:
        """Return the standing stop of ``scope`` as (journal sequence, epoch), or ``None``."""
        stops = self._stops.get(scope)
        if stops is None or stops.standing is None:
            return None
        return stops.standing, stops.epoch

    def epoch(self, scope: str) -> int:
        """Return the epoch of the latest stop of ``scope``; 0 when it was never stopped."""
        stops = self._stops.get(scope)
        return 0 if stops is None else stops.epoch

    def _scopes_of(self, operation_id: str) -> tuple[str, ...]:
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
        for scope in self._scopes_of(operation_id):
            stops = self._stops.get(scope)
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

    def open_in(self, scope: str) -> tuple[OpenAttempt, ...]:
        """Return every attempt open in an operation that ``scope`` contains."""
        found: list[OpenAttempt] = []
        for operation_id, entry in self._operations.items():
            if scope not in self._scopes_of(operation_id):
                continue
            found.extend(
                OpenAttempt(operation_id, attempt.attempt_id, entry.fact.task_id, attempt.executor)
                for attempt in entry.attempts
                if not attempt.ended
            )
        return tuple(found)

    def actors(self, scope: str) -> frozenset[str]:
        """Return the actors of ``scope``: the declarers of its listed or open operations."""
        stops = self._stops.get(scope)
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

    def note_outcome(self, operation_id: str, sender: str, state: str) -> None:
        """End the open attempt when its own executor reports a terminal state."""
        entry = self._operations.get(operation_id)
        if entry is None or state not in TERMINAL_OUTCOME_STATES:
            return
        for attempt in entry.attempts:
            if not attempt.ended and attempt.executor == sender:
                attempt.ended = True

    def note_verification(
        self, operation_id: str, sender: str, *, effect: str, result: str
    ) -> None:
        """Reconcile ended attempts, and close the operation when it is verified."""
        entry = self._operations.get(operation_id)
        if entry is None or sender == entry.fact.declarer:
            return
        if result == "verified":
            entry.closed = True
        if effect in OBSERVED_EFFECTS:
            for attempt in entry.attempts:
                if attempt.ended:
                    attempt.reconciled = True

    def note_seal(self, operation_id: str, attempt_id: str) -> None:
        """End the open attempt ``attempt_id`` as sealed; an ended one is left as it is."""
        entry = self._operations.get(operation_id)
        if entry is None:
            return
        for attempt in entry.attempts:
            if attempt.attempt_id == attempt_id and not attempt.ended:
                attempt.ended = attempt.sealed = True

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
        return AttemptView(
            closed=entry.closed,
            attempt_open=any(not attempt.ended for attempt in entry.attempts),
            approval_consumed=any(
                attempt.approval_seq == approval_seq for attempt in entry.attempts
            ),
            unreconciled=any(
                attempt.ended and not attempt.reconciled for attempt in entry.attempts
            ),
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
        """Return ``open``, ``sealed``, ``ended``, ``reconciled``, ``closed`` or ``none``."""
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
        return STATE_ENDED if found.ended else STATE_OPEN
