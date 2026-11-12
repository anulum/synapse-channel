# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — answer a request to open an attempt and journal the answer
"""Serve ``operation_attempt``: one journalled answer per request, opened attempts once.

The hub decodes the request (a malformed one, or one without ``idem_key``, is
answered ``invalid_request`` and journals nothing), then runs one keyed atomic
operation under its mutation actor. Inside it the hub reads the claim, the
declared operation and its attempts, the current approval and the verdict
gate, decides with :func:`~synapse_channel.core.operation_attempts.decide`,
and for ``approved_fresh`` opens the attempt and consumes the approval. The
answer row and the stored answer are committed in one transaction under the
request's key; a repeated request receives the stored answer unchanged.

Every answer frame has two parts. The stored part is the answer as committed
and is byte-identical on a repeat. The ``live`` part is computed when the
frame is sent: the present state of the attempt the answer names and whether
the answer is a repeat. An executor starts its effect only on
``approved_fresh`` with an ``open`` attempt.

**Lock order.** The hub has one mutation actor, one lock. This handler takes it
once, through the atomic path, for the reading, the decision and the commit.
Operation records, claims and progress notes take the same lock once each. No
path holds it while waiting for another lock, so no cycle exists. Every input
of the decision is written under that lock, so none can be journalled between
the reading and the answer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import CLAIM, Target
from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_facts import current_approval
from synapse_channel.core.operation_attempt_ledger import STATE_NONE, AttemptLedger
from synapse_channel.core.operation_attempts import (
    APPROVED_FRESH,
    INVALID_REQUEST,
    AttemptFacts,
    AttemptRequest,
    Decision,
    OperationAttemptError,
    attempt_id,
    decide,
    decode_attempt_request,
)
from synapse_channel.core.operation_records import declaration_refusal
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import Target as AclTarget
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.operation_records import OperationLedger
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.state import SynapseState
    from synapse_channel.core.verdict_hub import VerdictHubPolicy

    class OperationAttemptContext(HandlerContext, Protocol):
        """Capabilities the attempt handler consumes beyond the shared ones."""

        @property
        def state(self) -> SynapseState:
            """Return the live claim state."""
            ...

        @property
        def verdict_policy(self) -> VerdictHubPolicy:
            """Return this hub's verdict rules."""
            ...

        @property
        def operation_records(self) -> OperationLedger:
            """Return the declared operations and their attempts."""
            ...


NO_JOURNAL = "no_journal"
"""The reply on a hub without a journal, which can neither journal nor open an attempt."""

wall_clock: Callable[[], float] = time.time
"""The hub's wall clock for this verb; one reading serves every comparison of a request."""


@dataclass(frozen=True)
class _Outcome:
    decision: Decision
    approval_seq: int | None
    opened: str
    now: float


def _facts(
    hub: OperationAttemptContext,
    store: EventStore,
    request: AttemptRequest,
    sender: str,
    attempts: AttemptLedger,
    now: float,
) -> AttemptFacts:
    """Read everything the decision needs, under the mutation lock."""
    approval = current_approval(store, hub.verdict_policy, request.intent_sha256)
    view = attempts.view(request.operation_id, None if approval is None else approval.seq)
    return AttemptFacts(
        claim_refusal=declaration_refusal(
            hub.state.claims.get(request.task_id), sender, request.epoch, now
        ),
        operation=attempts.operation(request.operation_id),
        gate_enabled=hub.verdict_policy.gate_enabled,
        approval=approval,
        closed=view.closed,
        attempt_open=view.attempt_open,
        approval_consumed=view.approval_consumed,
        unreconciled=view.unreconciled,
        now=now,
    )


def _row(request: AttemptRequest, sender: str, outcome: _Outcome) -> dict[str, Any]:
    """Return the journal row of one answer."""
    row: dict[str, Any] = {
        "task_id": request.task_id,
        "operation_id": request.operation_id,
        "sender": sender,
        "intent_sha256": request.intent_sha256,
        "answer": outcome.decision.answer,
        "detail": outcome.decision.detail,
        "hub_time": outcome.now,
        "request_key": request.idem_key,
    }
    if outcome.opened:
        row["attempt_id"] = outcome.opened
        row["approval_seq"] = outcome.approval_seq
    return row


async def handle_operation_attempt(
    hub: OperationAttemptContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one request to open an attempt and journal the answer."""
    try:
        request = decode_attempt_request(data)
    except OperationAttemptError as exc:
        await _refuse(hub, sender, data, websocket, INVALID_REQUEST, str(exc))
        return
    store = hub.journal
    if store is None:
        await _refuse(hub, sender, data, websocket, NO_JOURNAL, "this hub keeps no journal")
        return
    attempts = hub.operation_records.attempts

    def mutate(candidate: AttemptLedger) -> _Outcome:
        now = wall_clock()
        facts = _facts(hub, store, request, sender, candidate, now)
        decision = decide(request, facts, requester=sender)
        if decision.answer != APPROVED_FRESH or facts.approval is None:
            return _Outcome(decision, None, "", now)
        opened = attempt_id(request.operation_id, facts.approval.seq)
        candidate.open_attempt(
            request.operation_id, opened, executor=sender, approval_seq=facts.approval.seq
        )
        return _Outcome(decision, facts.approval.seq, opened, now)

    def prepare(outcome: _Outcome) -> OperationDraft:
        stored = hub.system(
            outcome.decision.detail,
            msg_type=MessageType.OPERATION_ATTEMPT_RESULT,
            target=sender,
            answer=outcome.decision.answer,
            seq=0,
            attempt_id=outcome.opened,
            approval_seq=outcome.approval_seq,
            intent_sha256=request.intent_sha256,
            hub_time=outcome.now,
            task_id=request.task_id,
            operation_id=request.operation_id,
            request_key=request.idem_key,
        )
        return OperationDraft(
            response=stored,
            events=((EventKind.OPERATION_ATTEMPT_ANSWERED, _row(request, sender, outcome)),),
            intent={"family": "operation_attempt"},
            response_event_seq_field="seq",
        )

    execution = await hub.run_atomic_operation(
        data, mutate, prepare, subject=attempts, publish_candidate=attempts.publish_from
    )
    assert execution is not None and execution.response is not None
    if execution.outcome == "conflict":
        await hub.send_json(websocket, execution.response)
        return
    await hub.send_json(websocket, _with_live_part(execution.response, attempts, execution.outcome))
    await hub.settle_atomic_operation(data)


def _with_live_part(
    stored: dict[str, Any], attempts: AttemptLedger, outcome: str
) -> dict[str, Any]:
    """Return the stored answer with the attempt's present state beside it."""
    opened = str(stored.get("attempt_id") or "")
    state = (
        attempts.attempt_state(str(stored.get("operation_id") or ""), opened)
        if opened
        else STATE_NONE
    )
    return {**stored, "live": {"attempt_state": state, "replayed": outcome == "replayed"}}


async def _refuse(
    hub: OperationAttemptContext,
    sender: str,
    data: dict[str, Any],
    websocket: Any,
    answer: str,
    detail: str,
) -> None:
    """Answer a request that never reached the decision; nothing is journalled."""
    await hub.send_json(
        websocket,
        hub.system(
            detail,
            msg_type=MessageType.OPERATION_ATTEMPT_RESULT,
            target=sender,
            answer=answer,
            task_id=str(data.get("task_id") or ""),
            operation_id=str(data.get("operation_id") or ""),
            request_key=str(data.get("idem_key") or ""),
            live={"attempt_state": STATE_NONE, "replayed": False},
        ),
    )


def operation_attempt_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Keep a loaded policy's claim rule in force for the task the request names."""
    return [(CLAIM, Target("claim", str(data.get("task_id") or "")))]


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.OPERATION_ATTEMPT,),
        handler=handle_operation_attempt,
        reply_types=(MessageType.OPERATION_ATTEMPT_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=operation_attempt_access,
        event_kinds=(EventKind.OPERATION_ATTEMPT_ANSWERED,),
        minimum_wire_version=7,
        commands=("operation",),
    ),
)
