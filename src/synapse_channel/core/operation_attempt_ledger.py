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
* after the end, a verification by a sender other than the declarer with the
  effect ``observed`` or ``absent`` reconciles it;
* a verification by a sender other than the declarer with the result
  ``verified`` closes the operation.

A verification by the declarer is kept by the journal and counts for nothing
here. Restoring checks no time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempts import APPROVED_FRESH, OperationFact

if TYPE_CHECKING:
    from synapse_channel.core.persistence import EventStore, StoredEvent

TERMINAL_OUTCOME_STATES = frozenset({"not_started", "confirmed_done", "confirmed_failed"})
"""Outcome states that end the executor's own attempt."""

OBSERVED_EFFECTS = frozenset({"observed", "absent"})
"""Verification effects that reconcile an ended attempt."""

STATE_NONE = "none"
STATE_OPEN = "open"
STATE_ENDED = "ended"
STATE_RECONCILED = "reconciled"
STATE_CLOSED = "closed"

_RESTORED_KINDS = (
    EventKind.OPERATION_DECLARED,
    EventKind.OPERATION_OUTCOME,
    EventKind.OPERATION_VERIFIED,
    EventKind.OPERATION_ATTEMPT_ANSWERED,
)


@dataclass
class _Attempt:
    attempt_id: str
    executor: str
    approval_seq: int
    ended: bool = False
    reconciled: bool = False


@dataclass
class _Operation:
    fact: OperationFact
    attempts: list[_Attempt] = field(default_factory=list)
    closed: bool = False


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
        if store is not None:
            for event in store.iter_events(kinds=_RESTORED_KINDS):
                self.apply_event(event)

    @property
    def operations(self) -> dict[str, _Operation]:
        """Return the live table, for :meth:`publish_from` of the hub's actor."""
        return self._operations

    def publish_from(self, other: AttemptLedger) -> None:
        """Install the table of a candidate that the hub's actor has made durable."""
        self._operations = other.operations

    def apply_event(self, event: StoredEvent) -> None:
        """Apply one journalled row; a row that lacks a field the hub writes is skipped."""
        payload = event.payload
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

    def _restore_declaration(
        self, operation_id: str, sender: str, payload: Mapping[str, Any]
    ) -> None:
        task_id, declared_at = payload.get("task_id"), payload.get("recorded_at")
        if not isinstance(task_id, str) or not isinstance(declared_at, (int, float)):
            return
        digest = payload.get("intent_sha256")
        self.note_declaration(
            operation_id,
            OperationFact(
                task_id=task_id,
                declarer=sender,
                intent_sha256=digest if isinstance(digest, str) else None,
                deadline=_deadline_of(payload.get("intent")),
                declared_at=float(declared_at),
            ),
        )

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

    def attempt_state(self, operation_id: str, attempt_id: str) -> str:
        """Return ``open``, ``ended``, ``reconciled``, ``closed`` or ``none`` for one attempt."""
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
        return STATE_ENDED if found.ended else STATE_OPEN
