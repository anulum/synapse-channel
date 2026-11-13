# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serve operation records requested over the wire
"""Serve ``operation_record``: journal a declaration, an outcome, a verification or a seal.

The hub:

1. refuses a malformed request and a hub without a journal;
2. for ``declare``, refuses an authorisation intent that is malformed
   (``intent_invalid``) or whose deadline is not ahead of the hub's clock
   (``intent_expired``), requires the sender to own the live claim at the
   stated epoch (or its ownership epoch) and refuses an operation id already
   declared; an accepted intent is composed with the sender as its actor;
3. for ``outcome`` and ``verification``, requires the ``operation-evidence``
   permission for the task (enforced here even when ``--require-acl`` is off,
   so an open hub never accepts third-party evidence), a declared operation on
   the same task, a body naming the same operation, the verifier matching the
   declared effect, and fewer than 32 earlier rows for the operation;
4. for ``seal``, requires the ``operation-seal`` permission for the task (also
   enforced here on an open hub; no identity holds it by default), a declared
   operation on the same task whose named attempt is open, fewer than 32
   earlier rows, and a record naming exactly that attempt's ``sealed`` marker,
   which the hub recomputes; a kept seal ends the attempt without reconciling it;
5. journals one event and answers the sender privately with an
   ``operation_record_result`` naming the status, the reason, the sequence and,
   for a declaration that kept an intent, its ``intent_sha256``.

The check and the journal row run under the hub's mutation actor, the lock that
also orders claims, progress notes and requests to open an attempt, so a record
can never be journalled between an attempt's reading of its inputs and its
answer. Each accepted record is also given to the ledger of attempts: a
declaration names the declarer, an outcome may end its executor's attempt, and a
verification may reconcile an ended attempt or close the operation
(:mod:`synapse_channel.core.operation_attempt_ledger`).

Records never grant authority. They are observations a reader can weigh. An
intent grants nothing either: an approval may name its digest, and what that
approval admits is decided elsewhere.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import (
    CLAIM,
    OPERATION_EVIDENCE,
    OPERATION_SEAL,
    Target,
    task_grant,
)
from synapse_channel.core.authorisation_intent import (
    REFUSED_INTENT_EXPIRED,
    REFUSED_INTENT_INVALID,
    AuthorisationIntent,
    AuthorisationIntentError,
    compose_intent,
    intent_time_refusal,
)
from synapse_channel.core.handlers.operation_attempts import VERB_SPECS as ATTEMPT_VERB_SPECS
from synapse_channel.core.handlers.operation_state import VERB_SPECS as STATE_VERB_SPECS
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempts import OperationFact
from synapse_channel.core.operation_records import (
    RECORD_DECLARE,
    RECORD_SEAL,
    REFUSED_DUPLICATE,
    REFUSED_INVALID,
    REFUSED_NO_JOURNAL,
    REFUSED_NOT_AUTHORISED,
    REFUSED_NOT_HOLDER,
    STATUS_ACCEPTED,
    STATUS_REFUSED,
    OperationRecordError,
    OperationRecordRequest,
    declaration_refusal,
    decode_operation_record,
    evidence_refusal,
    record_operation,
    seal_refusal,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import AclPolicy
    from synapse_channel.core.acl import Target as AclTarget
    from synapse_channel.core.operation_records import OperationLedger
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.state import SynapseState
    from synapse_channel.core.state_models import TaskClaim
    from synapse_channel.core.state_transaction import SerializedStateMutationActor

    class OperationRecordContext(Protocol):
        """Capabilities consumed by the operation record handler."""

        @property
        def journal(self) -> EventStore | None:
            """Return the hub journal, or ``None`` when the hub keeps none."""
            ...

        @property
        def state(self) -> SynapseState:
            """Return the live claim state."""
            ...

        @property
        def acl_policy(self) -> AclPolicy | None:
            """Return the loaded ACL policy, or ``None``."""
            ...

        @property
        def operation_records(self) -> OperationLedger:
            """Return the declared operations restored from the journal."""
            ...

        @property
        def state_mutations(self) -> SerializedStateMutationActor:
            """Return the hub's single mutation actor."""
            ...

        async def send_json(self, websocket: Any, frame: dict[str, Any]) -> None:
            """Send one frame to one socket."""
            ...

        def system(self, payload: str, **fields: Any) -> dict[str, Any]:
            """Build one hub frame."""
            ...


@dataclass(frozen=True)
class _Verdict:
    status: str
    detail: str
    reason: str = ""
    seq: int | None = None
    intent_sha256: str = ""


def _refused(reason: str, detail: str) -> _Verdict:
    return _Verdict(STATUS_REFUSED, detail, reason=reason)


_DEADLINE_REFUSALS = {
    REFUSED_INTENT_EXPIRED: "the intent's deadline is not after the hub's clock",
    REFUSED_INTENT_INVALID: "the intent's deadline lies more than 366 days after the hub's clock",
}
"""The detail of each refusal of an intent's deadline, by its reason."""


async def handle_operation_record(
    hub: OperationRecordContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Journal one accepted operation record and answer the sender privately."""
    try:
        request = decode_operation_record(data)
    except AuthorisationIntentError as exc:
        verdict = _refused(REFUSED_INTENT_INVALID, str(exc))
    except OperationRecordError as exc:
        verdict = _refused(REFUSED_INVALID, str(exc))
    else:
        attempts = hub.operation_records.attempts
        verdict = await hub.state_mutations.run_subject(
            attempts,
            lambda _attempts: _apply(hub, sender, request),
            publish_candidate=attempts.publish_from,
        )
    await hub.send_json(
        websocket,
        hub.system(
            verdict.detail,
            msg_type=MessageType.OPERATION_RECORD_RESULT,
            target=sender,
            status=verdict.status,
            reason=verdict.reason,
            seq=verdict.seq,
            intent_sha256=verdict.intent_sha256,
            record=str(data.get("record") or ""),
            task_id=str(data.get("task_id") or ""),
            operation_id=str(data.get("operation_id") or ""),
            request_id=str(data.get("request_id") or ""),
        ),
    )


def _apply(hub: OperationRecordContext, sender: str, request: OperationRecordRequest) -> _Verdict:
    """Check one request against the claim, the ledger and the policy; journal it."""
    store = hub.journal
    if store is None:
        return _refused(REFUSED_NO_JOURNAL, "this hub keeps no journal")
    ledger = hub.operation_records
    if request.record == RECORD_DECLARE:
        return _declare(store, ledger, hub.state.claims.get(request.task_id), sender, request)
    if request.record == RECORD_SEAL:
        return _seal(hub, store, ledger, sender, request)
    if not task_grant(hub.acl_policy, sender, request.task_id, OPERATION_EVIDENCE):
        return _refused(
            REFUSED_NOT_AUTHORISED, f"{sender} lacks operation-evidence for {request.task_id}"
        )
    refusal = evidence_refusal(ledger, request)
    if refusal is not None:
        return _refused(refusal, f"the {request.record} record cannot be kept: {refusal}")
    seq = record_operation(store, request, sender=sender)
    ledger.add_record(request.operation_id)
    if request.outcome is not None:
        ledger.attempts.note_outcome(request.operation_id, sender, request.outcome.state.value)
    else:
        verification = request.verification
        assert verification is not None, "record_operation keeps only outcomes and verifications"
        ledger.attempts.note_verification(
            request.operation_id, sender, effect=verification.effect, result=verification.result
        )
    return _Verdict(STATUS_ACCEPTED, f"{request.record} recorded", seq=seq)


def _seal(
    hub: OperationRecordContext,
    store: EventStore,
    ledger: OperationLedger,
    sender: str,
    request: OperationRecordRequest,
) -> _Verdict:
    """Journal a seal of an open attempt from a holder of ``operation-seal``; end the attempt."""
    if not task_grant(hub.acl_policy, sender, request.task_id, OPERATION_SEAL):
        return _refused(
            REFUSED_NOT_AUTHORISED, f"{sender} lacks operation-seal for {request.task_id}"
        )
    refusal = seal_refusal(ledger, request)
    if refusal is not None:
        return _refused(refusal, f"the seal record cannot be kept: {refusal}")
    seq = record_operation(store, request, sender=sender)
    ledger.add_record(request.operation_id)
    seal = request.seal
    assert seal is not None, "seal_refusal kept a seal record with its body"
    ledger.attempts.note_seal(request.operation_id, seal.attempt_id)
    return _Verdict(STATUS_ACCEPTED, "seal recorded", seq=seq)


def _declare(
    store: EventStore,
    ledger: OperationLedger,
    claim: TaskClaim | None,
    sender: str,
    request: OperationRecordRequest,
) -> _Verdict:
    """Journal a declaration from the live claim holder at its epoch, once per id."""
    epoch, effect = request.epoch, request.effect
    if epoch is None or effect is None:
        return _refused(REFUSED_INVALID, "a declaration names an epoch and an effect")
    now = time.time()
    intent: AuthorisationIntent | None = None
    if request.intent is not None:
        timing = intent_time_refusal(request.intent, now)
        if timing is not None:
            return _refused(timing, _DEADLINE_REFUSALS[timing])
        try:
            intent = compose_intent(
                request.intent,
                task_id=request.task_id,
                operation_id=request.operation_id,
                effect=effect,
                target=request.target,
                actor=sender,
            )
        except AuthorisationIntentError as exc:
            return _refused(REFUSED_INTENT_INVALID, str(exc))
    refusal = declaration_refusal(claim, sender, epoch, now)
    if refusal is not None or claim is None:
        reason = refusal or REFUSED_NOT_HOLDER
        return _refused(reason, f"{sender} does not hold {request.task_id} at that epoch")
    if ledger.task_of(request.operation_id) is not None:
        return _refused(REFUSED_DUPLICATE, "this operation id is already declared")
    seq = record_operation(
        store, request, sender=sender, snapshot_epoch=claim.epoch, now=now, intent=intent
    )
    ledger.declare(request.operation_id, request.task_id, effect)
    ledger.attempts.note_declaration(
        request.operation_id,
        OperationFact(
            task_id=request.task_id,
            declarer=sender,
            intent_sha256=None if intent is None else intent.sha256,
            deadline=None if request.intent is None else request.intent.deadline,
            declared_at=now,
        ),
    )
    digest = "" if intent is None else intent.sha256
    return _Verdict(STATUS_ACCEPTED, "operation declared", seq=seq, intent_sha256=digest)


def operation_record_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Keep a loaded policy's claim rule in force for declarations.

    Evidence and seal records are checked by the handler itself against
    ``operation-evidence`` and ``operation-seal``, which no open-hub posture
    can waive.
    """
    task_id = str(data.get("task_id") or "")
    if data.get("record") == RECORD_DECLARE:
        return [(CLAIM, Target("claim", task_id))]
    if data.get("record") == RECORD_SEAL:
        return [(OPERATION_SEAL, Target("claim", task_id))]
    return [(OPERATION_EVIDENCE, Target("claim", task_id))]


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.OPERATION_RECORD,),
        handler=handle_operation_record,
        reply_types=(MessageType.OPERATION_RECORD_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=operation_record_access,
        event_kinds=(
            EventKind.OPERATION_DECLARED,
            EventKind.OPERATION_OUTCOME,
            EventKind.OPERATION_VERIFIED,
            EventKind.OPERATION_SEALED,
        ),
        minimum_wire_version=7,
        commands=("operation",),
    ),
    *ATTEMPT_VERB_SPECS,
    *STATE_VERB_SPECS,
)
"""The operation family: records, requests to open an attempt, and reads of its attempts."""
