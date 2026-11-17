# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — set a scope's autonomy level by hand and journal every answer
"""Serve ``operation_level``: one journalled answer per request to set a scope's level.

The hub decodes the request (a malformed one is answered ``invalid_request``,
and a hub without a journal answers ``no_journal``; neither is journalled), then
runs one keyed atomic operation on the attempt ledger, under the mutation lock
that stops and requests to open attempts take.

* A permitted change of level is journalled as an ``operation_level`` row with
  the scope, the new ``level``, the level it came ``from``, the ``cause``
  (``by_hand``), the identity that ``granted_by`` it, and the scope's ``epoch``
  plus one. Its journal sequence becomes the scope's level line, so an
  automated approval from before it is stale; a person's approval is not.
* A raise is refused ``raise_stopped`` while the scope, or a scope containing
  it, stands stopped. A step down is never refused for a stop.
* Every other answer is journalled as an ``operation_level_answered`` row.

The request needs ``operation-level`` on the scope (target kind
``level-scope``): the access gate checks it under ``--require-acl``, and this
handler checks and journals it also when that is off.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import OPERATION_LEVEL, Target, scope_grant
from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import AttemptLedger
from synapse_channel.core.operation_attempts import INVALID_REQUEST
from synapse_channel.core.operation_levels import (
    ALREADY_AT_LEVEL,
    CAUSE_BY_HAND,
    LEVEL_RECORDED,
    NOT_PERMITTED,
    RAISE_STOPPED,
    LevelRequest,
    OperationLevelError,
    decode_level_request,
    rank,
)
from synapse_channel.core.operation_records import VERIFIER_FOR_EFFECT
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import AclPolicy
    from synapse_channel.core.acl import Target as AclTarget
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.operation_records import OperationLedger

    class OperationLevelContext(HandlerContext, Protocol):
        """Capabilities the level handler consumes beyond the shared ones."""

        @property
        def acl_policy(self) -> AclPolicy | None:
            """Return the loaded access file, or ``None``."""
            ...

        @property
        def operation_records(self) -> OperationLedger:
            """Return the declared operations, their attempts, stops and levels."""
            ...


NO_JOURNAL = "no_journal"
"""The reply on a hub without a journal, which records no level."""

LEVEL_TARGET = "level-scope"
"""The access target kind of a scope's level."""

wall_clock: Callable[[], float] = time.time
"""The hub's wall clock for this verb."""


@dataclass(frozen=True)
class _Outcome:
    """One answer, decided under the mutation lock."""

    answer: str
    detail: str
    now: float
    previous: str
    epoch: int = 0


@dataclass
class _Committed:
    """The decided answer, and the journal sequence of its row once committed."""

    outcome: _Outcome | None = None
    seq: int | None = None


def _decide(
    hub: OperationLevelContext, request: LevelRequest, sender: str, ledger: AttemptLedger
) -> _Outcome:
    now = wall_clock()
    scope, level = request.scope, request.level
    current, epoch = ledger.level_of(scope)
    if not scope_grant(hub.acl_policy, sender, scope, OPERATION_LEVEL, target_kind=LEVEL_TARGET):
        detail = f"{sender} holds no operation-level on {scope}"
        return _Outcome(NOT_PERMITTED, detail, now, current, epoch)
    if current == level:
        return _Outcome(ALREADY_AT_LEVEL, f"the scope {scope} is at {level}", now, current, epoch)
    stopped = ledger.stopped_within(scope) if rank(level) > rank(current) else None
    if stopped is not None:
        detail = f"the scope {stopped[0]} is stopped (journal seq {stopped[1]}); no raise"
        return _Outcome(RAISE_STOPPED, detail, now, current, epoch)
    detail = f"the scope {scope} is at {level} (from {current})"
    return _Outcome(LEVEL_RECORDED, detail, now, current, epoch + 1)


def _row(request: LevelRequest, sender: str, outcome: _Outcome) -> tuple[str, dict[str, Any]]:
    """Return the journal row of one answer: the level record, or the answered row."""
    common = {
        "scope": request.scope,
        "reason": request.reason,
        "hub_time": outcome.now,
        "request_key": request.idem_key,
    }
    if outcome.answer == LEVEL_RECORDED:
        return EventKind.OPERATION_LEVEL, {
            **common,
            "level": request.level,
            "from": outcome.previous,
            "cause": CAUSE_BY_HAND,
            "granted_by": sender,
            "epoch": outcome.epoch,
        }
    return EventKind.OPERATION_LEVEL_ANSWERED, {
        **common,
        "sender": sender,
        "level": request.level,
        "answer": outcome.answer,
        "detail": outcome.detail,
    }


async def handle_operation_level(
    hub: OperationLevelContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one request to set a scope's level and journal the answer."""
    key = str(data.get("idem_key") or "")
    try:
        request = decode_level_request(data, tuple(VERIFIER_FOR_EFFECT))
    except OperationLevelError as exc:
        await _refuse(hub, websocket, sender, INVALID_REQUEST, str(exc), "", key)
        return
    if hub.journal is None:
        detail = "this hub keeps no journal"
        await _refuse(hub, websocket, sender, NO_JOURNAL, detail, request.scope, key)
        return
    ledger = hub.operation_records.attempts
    committed = _Committed()

    def mutate(candidate: AttemptLedger) -> _Outcome:
        committed.outcome = _decide(hub, request, sender, candidate)
        return committed.outcome

    def finalize(response: dict[str, Any], sequences: tuple[int, ...]) -> Mapping[str, Any]:
        committed.seq = sequences[0]
        return {**response, "seq": sequences[0]}

    def prepare(outcome: _Outcome) -> OperationDraft:
        fields: dict[str, Any] = {
            "answer": outcome.answer,
            "scope": request.scope,
            "level": request.level,
            "from": outcome.previous,
            "epoch": outcome.epoch,
            "seq": 0,
            "hub_time": outcome.now,
            "request_key": request.idem_key,
        }
        stored = hub.system(
            outcome.detail, msg_type=MessageType.OPERATION_LEVEL_RESULT, target=sender, **fields
        )
        return OperationDraft(
            response=stored,
            events=(_row(request, sender, outcome),),
            intent={"family": "operation_level"},
            finalize_response=finalize,
        )

    def publish(candidate: AttemptLedger) -> None:
        """Note the committed level on the candidate, then install it."""
        outcome, seq = committed.outcome, committed.seq
        assert outcome is not None and seq is not None
        if outcome.answer == LEVEL_RECORDED:
            candidate.note_level(request.scope, seq, level=request.level, epoch=outcome.epoch)
        ledger.publish_from(candidate)

    execution = await hub.run_atomic_operation(
        data, mutate, prepare, subject=ledger, publish_candidate=publish
    )
    assert execution is not None and execution.response is not None
    if execution.outcome == "conflict":
        await hub.send_json(websocket, execution.response)
        return
    live = {"replayed": execution.outcome == "replayed"}
    await hub.send_json(websocket, {**execution.response, "live": live})
    await hub.settle_atomic_operation(data)


async def _refuse(
    hub: OperationLevelContext,
    websocket: Any,
    sender: str,
    answer: str,
    detail: str,
    scope: str,
    key: str,
) -> None:
    """Answer a request that never reached the decision; nothing is journalled."""
    await hub.send_json(
        websocket,
        hub.system(
            detail,
            msg_type=MessageType.OPERATION_LEVEL_RESULT,
            target=sender,
            answer=answer,
            scope=scope,
            request_key=key,
            live={"replayed": False},
        ),
    )


def operation_level_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Name ``operation-level`` on the scope; the handler checks it on an open hub too."""
    return [(OPERATION_LEVEL, Target(LEVEL_TARGET, str(data.get("scope") or "")))]


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.OPERATION_LEVEL,),
        handler=handle_operation_level,
        reply_types=(MessageType.OPERATION_LEVEL_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=operation_level_access,
        event_kinds=(EventKind.OPERATION_LEVEL, EventKind.OPERATION_LEVEL_ANSWERED),
        minimum_wire_version=7,
        commands=("operation",),
    ),
)
"""Setting a scope's autonomy level by hand, answered and journalled by its own handler."""
