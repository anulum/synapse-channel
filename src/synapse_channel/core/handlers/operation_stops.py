# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stop and resume a scope of operations and journal every answer
"""Serve ``operation_stop`` and ``operation_resume``: one journalled answer per request.

The hub decodes the request (a malformed one is answered ``invalid_request``, and
a hub without a journal answers ``no_journal``; neither is journalled), then runs
one keyed atomic operation under its mutation actor, on the same ledger the
requests to open an attempt mutate.

* A stop of a scope not already stopped is journalled as an
  ``operation_stopped`` row. The row lists every attempt open in the scope when
  it commits: no attempt can open between the list and the row, because both
  are made under the one mutation lock.
* A resume that is permitted, of a stopped scope, by a resumer who is not an
  actor of it, is journalled as an ``operation_resumed`` row.
* Every other answer is journalled as an ``operation_stop_answered`` row.

A repeated request with the same ``idem_key`` receives the stored answer.

**Authority.** Both requests name their permission on target kind
``stop-scope`` to the hub's access gate, as every guarded request does. A hub
that enforces its access file (``--require-acl``) refuses a request without the
grant there, before the verb, and journals nothing, as for every guarded
request. On an open hub any authenticated identity may stop, because stopping
is a step down. A resume needs ``operation-resume`` on the scope also there:
this handler checks it itself, and journals the refusal, so an open hub without
an access file cannot lift a stop. The stop answer says so when no access file
is loaded.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import OPERATION_RESUME, OPERATION_STOP, Target, scope_grant
from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import AttemptLedger, OpenAttempt
from synapse_channel.core.operation_attempts import INVALID_REQUEST
from synapse_channel.core.operation_records import VERIFIER_FOR_EFFECT
from synapse_channel.core.operation_stops import (
    ACTOR_OF_SCOPE,
    ALREADY_STOPPED,
    NOT_PERMITTED,
    NOT_STOPPED,
    RESUMED,
    RESUMED_FROM_STOP_REQUESTED,
    STOP_RECORDED,
    OperationStopError,
    ResumeRequest,
    StopRequest,
    decode_resume_request,
    decode_stop_request,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import AclPolicy
    from synapse_channel.core.acl import Target as AclTarget
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.operation_records import OperationLedger

    class OperationStopContext(HandlerContext, Protocol):
        """Capabilities the stop and resume handlers consume beyond the shared ones."""

        @property
        def acl_policy(self) -> AclPolicy | None:
            """Return the loaded access file, or ``None``."""
            ...

        @property
        def operation_records(self) -> OperationLedger:
            """Return the declared operations, their attempts and the stops."""
            ...


REQUEST_STOP = "stop"
REQUEST_RESUME = "resume"

NO_JOURNAL = "no_journal"
"""The reply on a hub without a journal, which can neither record nor lift a stop."""

NO_ACCESS_FILE_WARNING = (
    "this hub has no access file: lifting the stop needs a restart with an access file "
    "that grants operation-resume on the scope"
)
"""Said with a recorded stop on a hub that could not lift it as it runs."""

EFFECTS = tuple(VERIFIER_FOR_EFFECT)
"""The effect kinds an ``effect:`` scope may name: those a declaration may carry."""

wall_clock: Callable[[], float] = time.time
"""The hub's wall clock for these verbs."""


@dataclass(frozen=True)
class _Outcome:
    """One answer, decided under the mutation lock."""

    answer: str
    detail: str
    now: float
    epoch: int = 0
    listed: tuple[OpenAttempt, ...] = ()
    earlier_seq: int | None = None
    """The standing stop's sequence: the one already standing, or the one a resume lifts."""


@dataclass
class _Committed:
    """The decided answer, and the journal sequence of its row once committed."""

    outcome: _Outcome | None = None
    seq: int | None = None


def _listed(attempts: tuple[OpenAttempt, ...]) -> list[dict[str, str]]:
    return [
        {
            "operation_id": item.operation_id,
            "attempt_id": item.attempt_id,
            "task_id": item.task_id,
            "executor": item.executor,
        }
        for item in attempts
    ]


def _decide_stop(request: StopRequest, ledger: AttemptLedger) -> _Outcome:
    now = wall_clock()
    scope = request.scope
    standing = ledger.standing_stop(scope)
    if standing is not None:
        detail = f"the scope {scope} is already stopped (journal seq {standing[0]})"
        return _Outcome(ALREADY_STOPPED, detail, now, epoch=standing[1], earlier_seq=standing[0])
    listed = ledger.open_in(scope)
    detail = f"the scope {scope} is stopped; {len(listed)} open attempt(s) listed"
    return _Outcome(STOP_RECORDED, detail, now, epoch=ledger.epoch(scope) + 1, listed=listed)


def _decide_resume(
    hub: OperationStopContext, request: ResumeRequest, sender: str, ledger: AttemptLedger
) -> _Outcome:
    now = wall_clock()
    scope = request.scope
    if not scope_grant(hub.acl_policy, sender, scope, OPERATION_RESUME):
        return _Outcome(NOT_PERMITTED, f"{sender} holds no operation-resume on {scope}", now)
    standing = ledger.standing_stop(scope)
    if standing is None:
        return _Outcome(NOT_STOPPED, f"the scope {scope} is not stopped", now)
    if sender in ledger.actors(scope):
        detail = f"{sender} declared an operation the stop of {scope} covers"
        return _Outcome(ACTOR_OF_SCOPE, detail, now, epoch=standing[1], earlier_seq=standing[0])
    detail = f"the scope {scope} is resumed (the stop at journal seq {standing[0]} is lifted)"
    return _Outcome(RESUMED, detail, now, epoch=standing[1], earlier_seq=standing[0])


def _row(
    kind: str, request: StopRequest | ResumeRequest, sender: str, outcome: _Outcome
) -> tuple[str, dict[str, Any]]:
    """Return the journal row of one answer: the record itself, or the answered row."""
    common = {
        "scope": request.scope,
        "reason": request.reason,
        "hub_time": outcome.now,
        "request_key": request.idem_key,
    }
    if outcome.answer == STOP_RECORDED:
        row = {**common, "stopper": sender, "epoch": outcome.epoch}
        return EventKind.OPERATION_STOPPED, {**row, "listed": _listed(outcome.listed)}
    if outcome.answer == RESUMED and isinstance(request, ResumeRequest):
        row = {**common, "resumer": sender, "lifts_seq": outcome.earlier_seq}
        return EventKind.OPERATION_RESUMED, {
            **row,
            "resumed_from": RESUMED_FROM_STOP_REQUESTED,
            "case": request.case,
        }
    answered = {**common, "sender": sender, "request": kind, "answer": outcome.answer}
    answered["detail"] = outcome.detail
    if outcome.earlier_seq is not None:
        answered["standing_seq"] = outcome.earlier_seq
    if isinstance(request, ResumeRequest):
        answered["case"] = request.case
    return EventKind.OPERATION_STOP_ANSWERED, answered


def _answer_fields(
    hub: OperationStopContext, kind: str, request: StopRequest | ResumeRequest, outcome: _Outcome
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "request": kind,
        "answer": outcome.answer,
        "scope": request.scope,
        "seq": 0,
        "hub_time": outcome.now,
        "request_key": request.idem_key,
    }
    if outcome.answer == STOP_RECORDED:
        fields["epoch"] = outcome.epoch
        fields["listed"] = _listed(outcome.listed)
        if hub.acl_policy is None:
            fields["warning"] = NO_ACCESS_FILE_WARNING
    elif outcome.answer == RESUMED:
        fields["lifts_seq"] = outcome.earlier_seq
        fields["resumed_from"] = RESUMED_FROM_STOP_REQUESTED
    elif outcome.earlier_seq is not None:
        fields["standing_seq"] = outcome.earlier_seq
    if isinstance(request, ResumeRequest):
        fields["case"] = request.case
    return fields


def _decoder(kind: str) -> Callable[[Mapping[str, Any]], StopRequest | ResumeRequest]:
    if kind == REQUEST_STOP:
        return lambda data: decode_stop_request(data, EFFECTS)
    return lambda data: decode_resume_request(data, EFFECTS)


async def _serve(
    hub: OperationStopContext, kind: str, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one request to stop or resume a scope and journal the answer."""
    key = str(data.get("idem_key") or "")
    try:
        request = _decoder(kind)(data)
    except OperationStopError as exc:
        await _refuse(hub, websocket, sender, kind, INVALID_REQUEST, str(exc), "", key)
        return
    if hub.journal is None:
        detail = "this hub keeps no journal"
        await _refuse(hub, websocket, sender, kind, NO_JOURNAL, detail, request.scope, key)
        return
    ledger = hub.operation_records.attempts
    committed = _Committed()

    def mutate(candidate: AttemptLedger) -> _Outcome:
        if isinstance(request, ResumeRequest):
            committed.outcome = _decide_resume(hub, request, sender, candidate)
        else:
            committed.outcome = _decide_stop(request, candidate)
        return committed.outcome

    def finalize(response: dict[str, Any], sequences: tuple[int, ...]) -> Mapping[str, Any]:
        committed.seq = sequences[0]
        return {**response, "seq": sequences[0]}

    def prepare(outcome: _Outcome) -> OperationDraft:
        stored = hub.system(
            outcome.detail,
            msg_type=MessageType.OPERATION_STOP_RESULT,
            target=sender,
            **_answer_fields(hub, kind, request, outcome),
        )
        return OperationDraft(
            response=stored,
            events=(_row(kind, request, sender, outcome),),
            intent={"family": "operation_stop"},
            finalize_response=finalize,
        )

    def publish(candidate: AttemptLedger) -> None:
        """Note the committed record on the candidate, then install it."""
        outcome, seq = committed.outcome, committed.seq
        assert outcome is not None and seq is not None
        if outcome.answer == STOP_RECORDED:
            names = tuple(dict.fromkeys(item.operation_id for item in outcome.listed))
            candidate.note_stop(request.scope, seq, epoch=outcome.epoch, listed=names)
        elif outcome.answer == RESUMED:
            candidate.note_resume(request.scope, seq)
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
    hub: OperationStopContext,
    websocket: Any,
    sender: str,
    kind: str,
    answer: str,
    detail: str,
    scope: str,
    key: str,
) -> None:
    """Answer a request that never reached the decision; nothing is journalled.

    A malformed scope is not echoed: ``scope`` is empty for ``invalid_request``.
    """
    await hub.send_json(
        websocket,
        hub.system(
            detail,
            msg_type=MessageType.OPERATION_STOP_RESULT,
            target=sender,
            request=kind,
            answer=answer,
            scope=scope,
            request_key=key,
            live={"replayed": False},
        ),
    )


async def handle_operation_stop(
    hub: OperationStopContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one request to stop a scope and journal the answer."""
    await _serve(hub, REQUEST_STOP, sender, data, websocket)


async def handle_operation_resume(
    hub: OperationStopContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one request to resume a scope and journal the answer."""
    await _serve(hub, REQUEST_RESUME, sender, data, websocket)


def operation_stop_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Name ``operation-stop`` on the scope, which an enforcing hub's access gate checks."""
    return [(OPERATION_STOP, Target("stop-scope", str(data.get("scope") or "")))]


def operation_resume_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Name ``operation-resume`` on the scope; the handler checks it on an open hub too."""
    return [(OPERATION_RESUME, Target("stop-scope", str(data.get("scope") or "")))]


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.OPERATION_STOP,),
        handler=handle_operation_stop,
        reply_types=(MessageType.OPERATION_STOP_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=operation_stop_access,
        event_kinds=(EventKind.OPERATION_STOPPED, EventKind.OPERATION_STOP_ANSWERED),
        minimum_wire_version=7,
        commands=("operation",),
    ),
    VerbSpec(
        request_types=(MessageType.OPERATION_RESUME,),
        handler=handle_operation_resume,
        reply_types=(MessageType.OPERATION_STOP_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=operation_resume_access,
        event_kinds=(EventKind.OPERATION_RESUMED, EventKind.OPERATION_STOP_ANSWERED),
        minimum_wire_version=7,
        commands=("operation",),
    ),
)
"""The stop and resume of a scope, each answered and journalled by its own handler."""
