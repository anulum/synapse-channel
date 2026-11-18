# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an actor of a scope asks for its own step down
"""Serve ``operation_level_lower``: an actor of a scope lowers its level, as a trigger.

The request names a ``scope``, a ``level`` below the scope's current one, a
``reason`` and ``idem_key``, as ``operation_level`` does. An actor of the
scope (the declarer or an executor of an operation it contains) may always ask
for its own step down; the hub records it as an ``actor_request`` trigger with
its ``operation_level`` row in one transaction, under the state actor's lock.
Any other sender is refused ``not_an_actor``, and a level that is not below the
current one ``not_lower``; both refusals are journalled as
``operation_level_answered``. ``invalid_request`` and ``no_journal`` are not
journalled. A repeat finds the scope already lowered and is answered
``not_lower``.

Under ``--require-acl`` the access gate asks for ``operation-stop`` on the
scope (target kind ``stop-scope``): whoever may stop a scope may also lower it.
The handler checks the actor also without it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import OPERATION_STOP, Target
from synapse_channel.core.handlers.operation_triggers import record_trigger
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempts import INVALID_REQUEST
from synapse_channel.core.operation_levels import (
    LevelRequest,
    OperationLevelError,
    decode_level_request,
    rank,
)
from synapse_channel.core.operation_records import VERIFIER_FOR_EFFECT
from synapse_channel.core.operation_triggers import LOWERED, NOT_AN_ACTOR, NOT_LOWER
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import Target as AclTarget
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.operation_attempt_ledger import AttemptLedger
    from synapse_channel.core.operation_records import OperationLedger
    from synapse_channel.core.persistence import EventStore

    class LevelLowerContext(HandlerContext, Protocol):
        """Capabilities the step-down handler consumes beyond the shared ones."""

        @property
        def operation_records(self) -> OperationLedger:
            """Return the declared operations, their attempts, stops and levels."""
            ...


REQUEST_LOWER = "lower"
NO_JOURNAL = "no_journal"

wall_clock: Callable[[], float] = time.time
"""The hub's wall clock for this verb."""


@dataclass(frozen=True)
class _Answer:
    answer: str
    detail: str
    current: str
    seq: int
    now: float


def _decide(store: EventStore, live: AttemptLedger, request: LevelRequest, sender: str) -> _Answer:
    """Answer one request under the state actor's lock, writing its row or rows."""
    now = wall_clock()
    scope, level = request.scope, request.level
    current, _epoch = live.level_of(scope)
    if sender not in live.scope_actors(scope):
        answer, detail = NOT_AN_ACTOR, f"{sender} is not an actor of {scope}"
    elif rank(level) >= rank(current):
        answer, detail = NOT_LOWER, f"the scope {scope} is at {current}; {level} is not lower"
    else:
        trigger = live.actor_trigger(scope, level, sender)
        sequences = record_trigger(store, live, trigger, now)
        detail = f"the scope {scope} is at {level} (from {current}), at its actor's request"
        return _Answer(LOWERED, detail, current, sequences[0], now)
    row = {
        "request": REQUEST_LOWER,
        "scope": scope,
        "level": level,
        "reason": request.reason,
        "sender": sender,
        "answer": answer,
        "detail": detail,
        "hub_time": now,
        "request_key": request.idem_key,
    }
    seq = store.append(EventKind.OPERATION_LEVEL_ANSWERED, row, durable=True)
    return _Answer(answer, detail, current, seq, now)


async def handle_operation_level_lower(
    hub: LevelLowerContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one actor's request for its own step down and journal the answer."""
    key = str(data.get("idem_key") or "")
    try:
        request = decode_level_request(data, tuple(VERIFIER_FOR_EFFECT))
    except OperationLevelError as exc:
        await _reply(hub, websocket, sender, answer=INVALID_REQUEST, detail=str(exc), key=key)
        return
    store = hub.journal
    if store is None:
        detail = "this hub keeps no journal"
        await _reply(hub, websocket, sender, answer=NO_JOURNAL, detail=detail, key=key)
        return
    attempts = hub.operation_records.attempts
    result: _Answer = await hub.state_mutations.run_subject(
        attempts,
        lambda live: _decide(store, live, request, sender),
        publish_candidate=attempts.publish_from,
    )
    await _reply(
        hub,
        websocket,
        sender,
        answer=result.answer,
        detail=result.detail,
        key=key,
        scope=request.scope,
        level=request.level,
        **{"from": result.current},
        seq=result.seq,
        hub_time=result.now,
    )


async def _reply(
    hub: LevelLowerContext,
    websocket: Any,
    sender: str,
    *,
    answer: str,
    detail: str,
    key: str,
    **fields: Any,
) -> None:
    await hub.send_json(
        websocket,
        hub.system(
            detail,
            msg_type=MessageType.OPERATION_LEVEL_RESULT,
            target=sender,
            request=REQUEST_LOWER,
            answer=answer,
            request_key=key,
            **{"scope": "", **fields},
        ),
    )


def operation_level_lower_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Name ``operation-stop`` on the scope: whoever may stop a scope may also lower it."""
    return [(OPERATION_STOP, Target("stop-scope", str(data.get("scope") or "")))]


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.OPERATION_LEVEL_LOWER,),
        handler=handle_operation_level_lower,
        reply_types=(MessageType.OPERATION_LEVEL_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=operation_level_lower_access,
        event_kinds=(
            EventKind.OPERATION_TRIGGER,
            EventKind.OPERATION_LEVEL,
            EventKind.OPERATION_LEVEL_ANSWERED,
        ),
        minimum_wire_version=7,
        commands=(),
    ),
)
"""An actor's own step down, answered and journalled by its own handler."""
