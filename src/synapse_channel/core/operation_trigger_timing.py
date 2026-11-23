# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — when a trigger is due, read from the ledger's public queries
"""The trigger queries. They hold no table of their own."""

from __future__ import annotations

from typing import TYPE_CHECKING

from synapse_channel.core.operation_stop_states import STATE_STOP_REQUESTED
from synapse_channel.core.operation_triggers import (
    CAUSE_ACTOR_REQUEST,
    CAUSE_DEADLINE_UNVERIFIED,
    CAUSE_STOP_UNSETTLED,
    TRIGGER_TARGET,
    Trigger,
    above,
)

if TYPE_CHECKING:
    from synapse_channel.core.operation_attempt_ledger import AttemptLedger, _Operation
    from synapse_channel.core.operation_stop_ledger import _StopRow


def _past_deadline_unverified(entry: _Operation, now: float, since: float) -> bool:
    """Return whether a deadline passed in (since, now], ended, unverified and not yet triggered."""
    deadline = entry.fact.deadline
    return (
        deadline is not None
        and since < deadline <= now
        and not entry.closed
        and any(attempt.ended for attempt in entry.attempts)
        and CAUSE_DEADLINE_UNVERIFIED not in entry.triggered
    )


def _levels_of(ledger: AttemptLedger, scopes: tuple[str, ...]) -> tuple[tuple[str, str, int], ...]:
    """Return each scope with the level and epoch the ledger publishes."""
    return tuple((scope, *ledger.level_of(scope)) for scope in scopes)


def operation_trigger(
    ledger: AttemptLedger, operation_id: str, cause: str, *, shown_by: int = 0
) -> Trigger:
    """Return the trigger ``cause`` of an operation: every containing scope above A1 falls."""
    lowers = above(_levels_of(ledger, ledger.operation_scopes(operation_id)), TRIGGER_TARGET)
    subject = {"operation_id": operation_id}
    scope = f"operation:{operation_id}"
    return Trigger(cause, scope, TRIGGER_TARGET, lowers, shown_by, "", subject)


def _unsettled(
    ledger: AttemptLedger, seq: int, row: _StopRow, now: float, bound: float, since: float
) -> bool:
    """Return whether a stop's bound passed in (since, now] and it is still requested."""
    passed = row.recorded_at + bound
    if row.bound_triggered or row.resumed is not None or not row.recorded_at > 0:
        return False
    if not since < passed <= now:
        return False
    state = ledger.stop_state(seq)
    return state is not None and state.state == STATE_STOP_REQUESTED


def due_triggers(
    ledger: AttemptLedger, now: float, stop_bound: float, since: float = 0.0
) -> list[Trigger]:
    """Return the triggers the watcher records at ``now``, once each.

    An operation past its intent's deadline with an ended attempt and no
    independent verification is ``deadline_unverified``; a stop still
    ``stop requested`` ``stop_bound`` seconds after it was recorded, and not
    lifted, is ``stop_unsettled``. Only a deadline or a bound that passed
    after ``since`` counts, so a watcher started on a long journal writes no
    burst of old triggers.
    """
    found = [
        operation_trigger(ledger, operation_id, CAUSE_DEADLINE_UNVERIFIED)
        for operation_id, entry in sorted(ledger.operations.items())
        if _past_deadline_unverified(entry, now, since)
    ]
    for seq, row in sorted(ledger.stop_rows.items()):
        if _unsettled(ledger, seq, row, now, stop_bound, since):
            lowers = above(_levels_of(ledger, (row.scope,)), TRIGGER_TARGET)
            subject = {"stop_seq": seq}
            found.append(
                Trigger(CAUSE_STOP_UNSETTLED, row.scope, TRIGGER_TARGET, lowers, seq, "", subject)
            )
    return found


def actor_trigger(ledger: AttemptLedger, scope: str, target: str, actor: str) -> Trigger:
    """Return an actor's own step down of ``scope`` to ``target``."""
    lowers = above(_levels_of(ledger, (scope,)), target)
    return Trigger(CAUSE_ACTOR_REQUEST, scope, target, lowers, 0, actor, {})
