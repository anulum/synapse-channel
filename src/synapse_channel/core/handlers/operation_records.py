# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serve operation records requested over the wire
"""Serve ``operation_record``: journal a declaration, an outcome or a verification.

The hub:

1. refuses a malformed request and a hub without a journal;
2. for ``declare``, requires the sender to own the live claim at the stated
   epoch (or its ownership epoch) and refuses an operation id already declared;
3. for ``outcome`` and ``verification``, requires the ``operation-evidence``
   permission for the task (enforced here even when ``--require-acl`` is off,
   so an open hub never accepts third-party evidence), a declared operation on
   the same task, a body naming the same operation, the verifier matching the
   declared effect, and fewer than 32 earlier rows for the operation;
4. journals one event and answers the sender privately with an
   ``operation_record_result`` naming the status, the reason and the sequence.

Records never grant authority. They are observations a reader can weigh.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import CLAIM, OPERATION_EVIDENCE, WOULD_ALLOW, Target, evaluate_access
from synapse_channel.core.identity_namespace import project_of
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_records import (
    RECORD_DECLARE,
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


def _refused(reason: str, detail: str) -> _Verdict:
    return _Verdict(STATUS_REFUSED, detail, reason=reason)


async def handle_operation_record(
    hub: OperationRecordContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Journal one accepted operation record and answer the sender privately."""
    try:
        request = decode_operation_record(data)
    except OperationRecordError as exc:
        verdict = _refused(REFUSED_INVALID, str(exc))
    else:
        verdict = _apply(hub, sender, request)
    await hub.send_json(
        websocket,
        hub.system(
            verdict.detail,
            msg_type=MessageType.OPERATION_RECORD_RESULT,
            target=sender,
            status=verdict.status,
            reason=verdict.reason,
            seq=verdict.seq,
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
    if not _evidence_allowed(hub, sender, request.task_id):
        return _refused(
            REFUSED_NOT_AUTHORISED, f"{sender} lacks operation-evidence for {request.task_id}"
        )
    refusal = evidence_refusal(ledger, request)
    if refusal is not None:
        return _refused(refusal, f"the {request.record} record cannot be kept: {refusal}")
    seq = record_operation(store, request, sender=sender)
    ledger.add_record(request.operation_id)
    return _Verdict(STATUS_ACCEPTED, f"{request.record} recorded", seq=seq)


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
    refusal = declaration_refusal(claim, sender, epoch, time.time())
    if refusal is not None or claim is None:
        reason = refusal or REFUSED_NOT_HOLDER
        return _refused(reason, f"{sender} does not hold {request.task_id} at that epoch")
    if ledger.task_of(request.operation_id) is not None:
        return _refused(REFUSED_DUPLICATE, "this operation id is already declared")
    seq = record_operation(store, request, sender=sender, snapshot_epoch=claim.epoch)
    ledger.declare(request.operation_id, request.task_id, effect)
    return _Verdict(STATUS_ACCEPTED, "operation declared", seq=seq)


def _evidence_allowed(hub: OperationRecordContext, sender: str, task_id: str) -> bool:
    """Return whether the policy grants ``sender`` ``operation-evidence`` on the task."""
    if hub.acl_policy is None:
        return False
    decision = evaluate_access(
        subject=sender,
        project=project_of(sender),
        permission=OPERATION_EVIDENCE,
        target=Target("claim", task_id),
        policy=hub.acl_policy,
    )
    return decision.decision == WOULD_ALLOW


def operation_record_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Keep a loaded policy's claim rule in force for declarations.

    Evidence records are checked by the handler itself against
    ``operation-evidence``, which no open-hub posture can waive.
    """
    task_id = str(data.get("task_id") or "")
    if data.get("record") == RECORD_DECLARE:
        return [(CLAIM, Target("claim", task_id))]
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
        ),
        minimum_wire_version=7,
        commands=("operation",),
    ),
)
