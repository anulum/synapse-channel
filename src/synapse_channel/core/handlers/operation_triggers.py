# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — record a trigger and its step down in one transaction; watch for triggers
"""Record each trigger with its step down in one transaction, and watch for the hub's own.

:func:`record_trigger` journals the ``operation_trigger`` row and each
``operation_level`` row of its step down in one SQLite transaction, the level
rows naming the trigger's sequence as their cause, then notes both on the live
ledger. Its callers hold the hub's state actor lock: the record handler for an
incorrect acceptance and an actor's own request. No approval is admitted and no
attempt opened between the trigger and the lower level.

:func:`watch_operation_triggers` runs only on a hub with ``--ladder on`` and a
journal. It ticks every
:data:`~synapse_channel.core.operation_triggers.WATCH_INTERVAL_SECONDS`; each
tick writes its triggers off the serving loop, in a worker thread, while it
holds the same lock. A failed tick is logged and the next one runs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_triggers import (
    WATCH_INTERVAL_SECONDS,
    Trigger,
    level_rows,
    trigger_row,
)

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.operation_attempt_ledger import AttemptLedger
    from synapse_channel.core.operation_records import OperationLedger
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.state_transaction import SerializedStateMutationActor

    class TriggerWatchContext(Protocol):
        """What the watcher reads of the hub."""

        @property
        def journal(self) -> EventStore | None:
            """Return the hub's journal, or ``None``."""
            ...

        @property
        def operation_records(self) -> OperationLedger:
            """Return the declared operations, their attempts, stops and levels."""
            ...

        @property
        def state_mutations(self) -> SerializedStateMutationActor:
            """Return the actor whose lock orders every durable mutation."""
            ...

        @property
        def stop_bound_seconds(self) -> float:
            """Return the seconds a stop may stay ``stop requested``."""
            ...

        @property
        def ladder_enabled(self) -> bool:
            """Return whether ``--ladder`` is on; the watcher runs only then."""
            ...


_LOG = logging.getLogger("synapse_channel.core.operation_triggers")

wall_clock: Callable[[], float] = time.time
"""The hub's wall clock for the watcher's deadlines and stop bounds."""


def write_trigger(store: EventStore, trigger: Trigger, now: float) -> tuple[int, ...]:
    """Journal ``trigger`` and its step down in one transaction; return their sequences.

    Returns
    -------
    tuple[int, ...]
        The trigger row's sequence, then each level row's, narrowest scope first.
    """
    return store.append_linked(
        (EventKind.OPERATION_TRIGGER, trigger_row(trigger, now)),
        lambda seq: level_rows(trigger, seq, now),
        durable=True,
    )


def note_written(attempts: AttemptLedger, trigger: Trigger, sequences: tuple[int, ...]) -> None:
    """Note a journalled trigger and its step down on ``attempts``."""
    for (scope, _level, epoch), seq in zip(trigger.lowers, sequences[1:], strict=True):
        attempts.note_level(scope, seq, level=trigger.target, epoch=epoch + 1)
    attempts.note_trigger(trigger.cause, trigger.subject)


def record_trigger(
    store: EventStore, attempts: AttemptLedger, trigger: Trigger, now: float
) -> tuple[int, ...]:
    """Journal ``trigger`` with its step down and note both on the live ``attempts``.

    For a caller that already holds the state actor's lock on the live ledger:
    the record handler and an actor's own request.
    """
    sequences = write_trigger(store, trigger, now)
    note_written(attempts, trigger, sequences)
    return sequences


async def tick(hub: TriggerWatchContext, since: float) -> tuple[Trigger, ...]:
    """Record each trigger whose deadline or stop bound passed after ``since``; return them.

    A read of the live ledger first finds whether anything is due, so a quiet
    tick takes no lock. Each due trigger is then decided again under the state
    actor's lock on a private copy, written in a worker thread off the serving
    loop, and published with its sequences; one trigger per transaction, so a
    failed write leaves every earlier trigger recorded and noted.
    """
    store = hub.journal
    if store is None:
        return ()
    attempts = hub.operation_records.attempts
    if not attempts.due_triggers(wall_clock(), hub.stop_bound_seconds, since):
        return ()
    recorded: list[Trigger] = []
    while (trigger := await _record_one(hub, store, attempts, since)) is not None:
        recorded.append(trigger)
    return tuple(recorded)


async def _record_one(
    hub: TriggerWatchContext, store: EventStore, attempts: AttemptLedger, since: float
) -> Trigger | None:
    """Decide, write and publish the first due trigger under the lock; ``None`` when none is due."""
    written: list[tuple[Trigger, tuple[int, ...]]] = []

    def mutate(candidate: AttemptLedger) -> tuple[Trigger, float] | None:
        now = wall_clock()
        due = candidate.due_triggers(now, hub.stop_bound_seconds, since)
        return (due[0], now) if due else None

    def persist(result: tuple[Trigger, float] | None) -> None:
        if result is not None:
            written.append((result[0], write_trigger(store, result[0], result[1])))

    def publish(candidate: AttemptLedger) -> None:
        for trigger, sequences in written:
            note_written(candidate, trigger, sequences)
        attempts.publish_from(candidate)

    result = await hub.state_mutations.run_subject(
        attempts, mutate, persist=persist, publish_candidate=publish
    )
    return None if result is None else result[0]


async def watch_operation_triggers(
    hub: TriggerWatchContext, interval: float = WATCH_INTERVAL_SECONDS
) -> None:
    """Tick every ``interval`` seconds while the hub serves; the loop never ends on its own.

    Only deadlines and stop bounds that pass after the watcher starts are
    triggers, so a hub restored from a long journal writes no burst of old ones.
    """
    since = wall_clock()
    while True:
        await asyncio.sleep(interval)
        try:
            await tick(hub, since)
        except Exception:
            _LOG.exception("cannot check the operations for triggers")
