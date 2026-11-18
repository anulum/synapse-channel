# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — declare a raise's criteria, judge them, and raise a level by one
"""Serve the three requests of a raise: criteria, judgement and raise, each journalled.

Each request runs as one keyed atomic operation on the attempt ledger, under
the mutation lock, and every answer but ``invalid_request`` and ``no_journal``
is a journalled row: the record itself, or ``operation_level_answered``.

* ``operation_level_criteria`` from a holder of ``operation-level`` on the
  scope, for the level one above the scope's current one, is journalled as
  ``level_criteria`` with the countable items and their digest.
* ``operation_level_judgement`` records, as ``level_judgement``, whether a
  judge found a criteria ``met``; the judge must not be an actor of the count
  (a declarer or an executor of what it counts, or the criteria's author). The
  judge's independence is recorded as ``unassessed``.
* ``operation_level_raise`` from a person holding ``operation-level`` raises the
  scope one level, as an ``operation_level`` row with cause
  ``judgement:<seq>``, only on a ``met`` judgement of this criteria by a judge
  who is not an actor, while no scope containing it is stopped and while the
  count, taken again now, still meets every item. A judgement from before the
  count's window (the criteria's sequence or the scope's latest step down) does
  not stand.

Criteria and a raise need ``operation-level`` on the scope (target kind
``level-scope``), which this handler checks also on an open hub. A judgement
needs ``operation-judge`` on the scope under ``--require-acl``; on an open hub
any identity that is not an actor of the count may judge, and no raise can
follow there, since no identity holds ``operation-level`` without an access file.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import OPERATION_JUDGE, OPERATION_LEVEL, Target, scope_grant
from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import (
    AttemptLedger,
    LevelCriteria,
    LevelJudgement,
)
from synapse_channel.core.operation_attempts import INVALID_REQUEST
from synapse_channel.core.operation_level_criteria import (
    CRITERIA_RECORDED,
    CRITERIA_UNKNOWN,
    CRITERIA_UNMET,
    INDEPENDENCE_UNASSESSED,
    JUDGE_IS_ACTOR,
    JUDGEMENT_MISSING,
    JUDGEMENT_RECORDED,
    MET,
    NOT_A_PERSON,
    NOT_ONE_LEVEL,
    NOT_PERMITTED,
    RAISED,
    SCOPE_STOPPED,
    UNCOUNTED,
    CriteriaRequest,
    JudgementRequest,
    LevelCriteriaError,
    RaiseRequest,
    criteria_digest,
    decode_criteria_request,
    decode_judgement_request,
    decode_raise_request,
    unmet,
)
from synapse_channel.core.operation_levels import KIND_PERSON, approver_kind, rank
from synapse_channel.core.operation_records import VERIFIER_FOR_EFFECT
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import AclPolicy
    from synapse_channel.core.acl import Target as AclTarget
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.operation_records import OperationLedger
    from synapse_channel.core.role_grants import RoleGrants

    class LevelRaiseContext(HandlerContext, Protocol):
        """Capabilities the raise handlers consume beyond the shared ones."""

        @property
        def acl_policy(self) -> AclPolicy | None:
            """Return the loaded access file, or ``None``."""
            ...

        @property
        def role_grants(self) -> RoleGrants | None:
            """Return the role grants, which tell a person from an automated role."""
            ...

        @property
        def operation_records(self) -> OperationLedger:
            """Return the declared operations, their attempts, stops and levels."""
            ...


REQUEST_CRITERIA = "criteria"
REQUEST_JUDGEMENT = "judgement"
REQUEST_RAISE = "raise"
NO_JOURNAL = "no_journal"
LEVEL_TARGET = "level-scope"
EFFECTS = tuple(VERIFIER_FOR_EFFECT)

wall_clock: Callable[[], float] = time.time
"""The hub's wall clock for these verbs; it dates deadlines in the count."""

_Request = CriteriaRequest | JudgementRequest | RaiseRequest


@dataclass(frozen=True)
class _Outcome:
    """One answer, decided under the mutation lock, with the row it journals."""

    answer: str
    detail: str
    now: float
    kind: str = EventKind.OPERATION_LEVEL_ANSWERED
    row: dict[str, Any] = field(default_factory=dict)
    note: Callable[[AttemptLedger, int], None] | None = None


@dataclass
class _Committed:
    outcome: _Outcome | None = None
    seq: int | None = None


def _refusal(answer: str, detail: str, now: float) -> _Outcome:
    return _Outcome(answer, detail, now)


def _granted(hub: LevelRaiseContext, sender: str, scope: str) -> bool:
    return scope_grant(hub.acl_policy, sender, scope, OPERATION_LEVEL, target_kind=LEVEL_TARGET)


def _criteria(
    hub: LevelRaiseContext, request: CriteriaRequest, sender: str, ledger: AttemptLedger
) -> _Outcome:
    now, scope, target = wall_clock(), request.scope, request.target
    if not _granted(hub, sender, scope):
        return _refusal(NOT_PERMITTED, f"{sender} holds no operation-level on {scope}", now)
    current, _epoch = ledger.level_of(scope)
    if rank(target) != rank(current) + 1:
        return _refusal(NOT_ONE_LEVEL, f"a raise of {scope} goes one level above {current}", now)
    digest = criteria_digest(scope, target, request.items)
    row = {
        "scope": scope,
        "target": target,
        "items": [item.to_json() for item in request.items],
        "digest": digest,
        "declared_by": sender,
    }
    criteria = request.items

    def note(candidate: AttemptLedger, seq: int) -> None:
        candidate.note_criteria(digest, LevelCriteria(seq, scope, target, criteria, sender))

    detail = f"criteria {digest} for {scope} at {target} recorded"
    return _Outcome(CRITERIA_RECORDED, detail, now, EventKind.LEVEL_CRITERIA, row, note)


def _actors(ledger: AttemptLedger, criteria: LevelCriteria) -> tuple[int, frozenset[str]]:
    since = ledger.window_start(criteria.scope, criteria.seq)
    return since, ledger.level_actors(criteria.scope, since) | {criteria.declared_by}


def _judgement(
    hub: LevelRaiseContext, request: JudgementRequest, sender: str, ledger: AttemptLedger
) -> _Outcome:
    now = wall_clock()
    criteria = ledger.criteria.get(request.digest)
    if criteria is None or criteria.scope != request.scope:
        return _refusal(CRITERIA_UNKNOWN, f"no criteria {request.digest} for {request.scope}", now)
    since, actors = _actors(ledger, criteria)
    if sender in actors:
        return _refusal(JUDGE_IS_ACTOR, f"{sender} is an actor of the count it would judge", now)
    store = hub.journal
    assert store is not None, "_serve answers no_journal before any decision"
    row = {
        "scope": criteria.scope,
        "digest": request.digest,
        "criteria_seq": criteria.seq,
        "verdict": request.verdict,
        "judge": sender,
        "independence": INDEPENDENCE_UNASSESSED,
        "evidence": {"from_seq": since, "to_seq": store.max_seq()},
    }
    judgement = LevelJudgement(request.digest, request.verdict, sender)

    def note(candidate: AttemptLedger, seq: int) -> None:
        candidate.note_judgement(seq, judgement)

    detail = f"criteria {request.digest} judged {request.verdict}"
    return _Outcome(JUDGEMENT_RECORDED, detail, now, EventKind.LEVEL_JUDGEMENT, row, note)


def _raise(
    hub: LevelRaiseContext, request: RaiseRequest, sender: str, ledger: AttemptLedger
) -> _Outcome:
    now, scope, target = wall_clock(), request.scope, request.target
    if not _granted(hub, sender, scope):
        return _refusal(NOT_PERMITTED, f"{sender} holds no operation-level on {scope}", now)
    if approver_kind(hub.role_grants, sender) != KIND_PERSON:
        return _refusal(NOT_A_PERSON, f"{sender} is not a person; a person grants a raise", now)
    current, epoch = ledger.level_of(scope)
    if rank(target) != rank(current) + 1:
        return _refusal(NOT_ONE_LEVEL, f"a raise of {scope} goes one level above {current}", now)
    stopped = ledger.stopped_within(scope)
    if stopped is not None:
        detail = f"the scope {stopped[0]} is stopped (journal seq {stopped[1]})"
        return _refusal(SCOPE_STOPPED, detail, now)
    judgement = ledger.judgements.get(request.judgement_seq)
    criteria = ledger.criteria.get(request.digest)
    if (
        judgement is None
        or criteria is None
        or judgement.digest != request.digest
        or judgement.verdict != MET
        or (criteria.scope, criteria.target) != (scope, target)
    ):
        detail = f"no met judgement at journal seq {request.judgement_seq} of this criteria"
        return _refusal(JUDGEMENT_MISSING, detail, now)
    since, actors = _actors(ledger, criteria)
    if request.judgement_seq <= since:
        detail = f"the judgement at journal seq {request.judgement_seq} predates the count's window"
        return _refusal(JUDGEMENT_MISSING, detail, now)
    if judgement.judge in actors:
        detail = f"the judge {judgement.judge} is an actor of the count"
        return _refusal(JUDGE_IS_ACTOR, detail, now)
    counts = ledger.level_counts(scope, since, now)
    missing = unmet(criteria.items, counts)
    if missing:
        return _refusal(CRITERIA_UNMET, "; ".join(missing), now)
    row = {
        "scope": scope,
        "level": target,
        "from": current,
        "cause": f"judgement:{request.judgement_seq}",
        "granted_by": sender,
        "epoch": epoch + 1,
        "criteria_digest": request.digest,
        "counts": counts,
        "uncounted": UNCOUNTED,
    }

    def note(candidate: AttemptLedger, seq: int) -> None:
        candidate.note_level(scope, seq, level=target, epoch=epoch + 1)

    detail = f"the scope {scope} is at {target} (from {current})"
    return _Outcome(RAISED, detail, now, EventKind.OPERATION_LEVEL, row, note)


_DECODERS: Mapping[str, Callable[[Mapping[str, Any]], _Request]] = {
    REQUEST_CRITERIA: lambda data: decode_criteria_request(data, EFFECTS),
    REQUEST_JUDGEMENT: lambda data: decode_judgement_request(data, EFFECTS),
    REQUEST_RAISE: lambda data: decode_raise_request(data, EFFECTS),
}


def _decide(
    hub: LevelRaiseContext, request: _Request, sender: str, ledger: AttemptLedger
) -> _Outcome:
    if isinstance(request, CriteriaRequest):
        return _criteria(hub, request, sender, ledger)
    if isinstance(request, JudgementRequest):
        return _judgement(hub, request, sender, ledger)
    return _raise(hub, request, sender, ledger)


def _row(kind: str, request: _Request, sender: str, outcome: _Outcome) -> dict[str, Any]:
    """Return the journal row of one answer: the record's own, or the answered row."""
    common = {"hub_time": outcome.now, "request_key": request.idem_key}
    if outcome.kind != EventKind.OPERATION_LEVEL_ANSWERED:
        return {**outcome.row, **common}
    return {
        **common,
        "request": kind,
        "scope": request.scope,
        "sender": sender,
        "answer": outcome.answer,
        "detail": outcome.detail,
    }


async def _serve(
    hub: LevelRaiseContext, kind: str, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    key = str(data.get("idem_key") or "")
    try:
        request = _DECODERS[kind](data)
    except LevelCriteriaError as exc:
        await _refuse(hub, websocket, sender, kind, INVALID_REQUEST, str(exc), "", key)
        return
    if hub.journal is None:
        detail = "this hub keeps no journal"
        await _refuse(hub, websocket, sender, kind, NO_JOURNAL, detail, request.scope, key)
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
            "request": kind,
            "answer": outcome.answer,
            "scope": request.scope,
            "seq": 0,
            "hub_time": outcome.now,
            "request_key": request.idem_key,
        }
        if outcome.kind != EventKind.OPERATION_LEVEL_ANSWERED:
            fields["record"] = dict(outcome.row)
        stored = hub.system(
            outcome.detail, msg_type=MessageType.OPERATION_LEVEL_RESULT, target=sender, **fields
        )
        return OperationDraft(
            response=stored,
            events=((outcome.kind, _row(kind, request, sender, outcome)),),
            intent={"family": "operation_level"},
            finalize_response=finalize,
        )

    def publish(candidate: AttemptLedger) -> None:
        outcome, seq = committed.outcome, committed.seq
        assert outcome is not None and seq is not None
        if outcome.note is not None:
            outcome.note(candidate, seq)
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
    hub: LevelRaiseContext,
    websocket: Any,
    sender: str,
    kind: str,
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
            request=kind,
            answer=answer,
            scope=scope,
            request_key=key,
            live={"replayed": False},
        ),
    )


async def handle_operation_level_criteria(
    hub: LevelRaiseContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one declaration of a raise's criteria and journal the answer."""
    await _serve(hub, REQUEST_CRITERIA, sender, data, websocket)


async def handle_operation_level_judgement(
    hub: LevelRaiseContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one judgement of a criteria and journal the answer."""
    await _serve(hub, REQUEST_JUDGEMENT, sender, data, websocket)


async def handle_operation_level_raise(
    hub: LevelRaiseContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one request to raise a scope's level and journal the answer."""
    await _serve(hub, REQUEST_RAISE, sender, data, websocket)


def _level_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Name ``operation-level`` on the scope; the handler checks it on an open hub too."""
    return [(OPERATION_LEVEL, Target(LEVEL_TARGET, str(data.get("scope") or "")))]


def _judge_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Name ``operation-judge`` on the scope, for the access gate under ``--require-acl``."""
    return [(OPERATION_JUDGE, Target(LEVEL_TARGET, str(data.get("scope") or "")))]


def _spec(request_type: str, handler: Any, accesses: Any, kinds: tuple[str, ...]) -> VerbSpec:
    return VerbSpec(
        request_types=(request_type,),
        handler=handler,
        reply_types=(MessageType.OPERATION_LEVEL_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=accesses,
        event_kinds=(*kinds, EventKind.OPERATION_LEVEL_ANSWERED),
        minimum_wire_version=7,
        commands=(),
    )


VERB_SPECS = (
    _spec(
        MessageType.OPERATION_LEVEL_CRITERIA,
        handle_operation_level_criteria,
        _level_access,
        (EventKind.LEVEL_CRITERIA,),
    ),
    _spec(
        MessageType.OPERATION_LEVEL_JUDGEMENT,
        handle_operation_level_judgement,
        _judge_access,
        (EventKind.LEVEL_JUDGEMENT,),
    ),
    _spec(
        MessageType.OPERATION_LEVEL_RAISE,
        handle_operation_level_raise,
        _level_access,
        (EventKind.OPERATION_LEVEL,),
    ),
)
"""A raise's criteria, its judgement and the raise, each answered and journalled."""
