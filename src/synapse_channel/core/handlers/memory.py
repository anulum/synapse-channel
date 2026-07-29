# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — persistent-memory write-side handlers (recall + findings)
"""Write-side handlers feeding an optional persistent-memory layer.

The hub stays memory-agnostic: it carries these records opaquely and never
indexes, ranks, or interprets them — a downstream adapter (e.g. REMANENTIA)
consumes the durable log. What the hub *does* add is the one thing only the
chokepoint every event passes through can: **attestation**. The producing
identity and the receive-time are stamped by the hub, not self-reported, so a
record cannot be back-dated or misattributed by its sender.

Two write-side surfaces share this module:

* **recall logging** — the query-stream: every lookup the fleet actually makes,
  captured as telemetry so a memory layer can calibrate recall against the *real*
  query distribution rather than activity-weighted noise. It is journalled but
  never broadcast.
* **findings** — the durable memory spine: authored atoms (facts, lessons,
  decisions, dead-ends, outcomes) that pass the emit gate before they are
  journalled. A finding the gate floors or admits is broadcast for fleet
  visibility; one it rejects is privately denied and never journalled.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from synapse_channel.core.atomic_operations import AtomicExecution, OperationDraft
from synapse_channel.core.emit_gate import REJECT, admit
from synapse_channel.core.finding import Finding
from synapse_channel.core.journal import EventKind, record_finding, record_recall
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub
    from synapse_channel.core.hub_ledger_guard import FindingQuota
    from synapse_channel.core.persistence import EventStore

logger = logging.getLogger("synapse.memory")

_JOURNAL_FAILURES = (sqlite3.Error, TypeError, ValueError, OSError)
"""Failures reported as a private non-commit rather than a socket-level error."""


@dataclass(frozen=True)
class _MemoryWrite:
    """One accepted or refused memory write prepared inside the mutation actor."""

    accepted: bool
    record: dict[str, Any] | None
    response: dict[str, Any]


def _required_response(execution: AtomicExecution) -> dict[str, Any]:
    """Return the actor-guaranteed exact replay or conflict response."""
    return cast(dict[str, Any], execution.response)


async def _send_journal_failure(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    subject: str,
    exc: BaseException,
) -> None:
    """Return a private error after a memory write failed before publication."""
    logger.error(
        "%s journal commit failed; memory mutation was not published", subject, exc_info=exc
    )
    await hub._send_json(
        websocket,
        hub._system(
            f"{subject} was not journalled; mutation rolled back.",
            msg_type=MessageType.ERROR,
            target=sender,
        ),
    )


async def handle_recall_log(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Journal one recall query-stream event and privately acknowledge the sender.

    The query and its outcome are taken from the message; the producing identity
    (``by``) and the receive-time (``at``) are stamped by the hub so they cannot
    be forged. The record is journalled when a durable log is attached and a
    private ``recall_logged`` ack returns to the sender.

    Parameters
    ----------
    hub : SynapseHub
        The coordination hub.
    sender : str
        The authenticated identity of the producing agent (used as ``by``).
    data : dict[str, Any]
        The recall envelope: ``query_text`` (the query), ``returned_claim_ids``
        (a list of memory ids returned), ``was_used`` (whether the answer was
        used), and ``abstained`` (whether the memory layer abstained).
    websocket : Any
        The sender's transport, for the private acknowledgement.
    """
    raw_ids = data.get("returned_claim_ids")
    returned = [str(c) for c in raw_ids] if isinstance(raw_ids, list) else []
    journal = cast("EventStore", hub.journal)

    def mutate(_subject: tuple[()]) -> _MemoryWrite:
        record = {
            "query_text": str(data.get("query_text") or ""),
            "returned_claim_ids": returned,
            "was_used": bool(data.get("was_used", False)),
            "abstained": bool(data.get("abstained", False)),
            "by": sender,
            "at": time.time(),
        }
        return _MemoryWrite(
            accepted=True,
            record=record,
            response=hub._system(
                "recall logged",
                msg_type=MessageType.RECALL_LOGGED,
                target=sender,
            ),
        )

    def persist(result: _MemoryWrite) -> None:
        record_recall(journal, cast(dict[str, Any], result.record))

    def prepare(result: _MemoryWrite) -> OperationDraft:
        record = cast(dict[str, Any], result.record)
        return OperationDraft(
            response=result.response,
            events=((EventKind.RECALL, record),),
            intent={"family": "recall_log", "response_type": MessageType.RECALL_LOGGED},
            response_event_seq_field="seq",
        )

    try:
        if hub.journal is not None and str(data.get("idem_key") or ""):
            execution = cast(
                AtomicExecution,
                await hub._run_atomic_operation(
                    data,
                    mutate,
                    prepare,
                    subject=(),
                    publish_candidate=lambda _candidate: None,
                ),
            )
        else:
            result = await hub.state_mutations.run_subject(
                (),
                mutate,
                persist=persist if hub.journal is not None else None,
                publish_candidate=lambda _candidate: None,
            )
            execution = AtomicExecution("uncommitted", result, None)
    except _JOURNAL_FAILURES as exc:
        await _send_journal_failure(hub, websocket, sender=sender, subject="Recall event", exc=exc)
        return

    if execution.outcome in {"replayed", "conflict"}:
        await hub._send_json(websocket, _required_response(execution))
        if execution.outcome == "replayed":
            await hub._settle_atomic_operation(data)
        return
    result = cast(_MemoryWrite, execution.mutation)
    response = execution.response or result.response
    await hub._send_json(websocket, response)
    if execution.outcome == "inserted":
        await hub._settle_atomic_operation(data)


async def handle_finding(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Admit one finding to the durable memory spine, or privately reject it.

    The finding is parsed from the message and run through the emit gate. A
    rejected record is refused with a private ``finding_rejected`` carrying the
    reasons, and nothing is journalled. An admitted or floored record is stamped
    with its hub-attested origin (the producing identity and receive-time, which
    the sender cannot forge), journalled durably, and broadcast as
    ``finding_recorded`` so the fleet sees it — carrying the verdict, the final
    claim status, and the stored record, so a producer whose claim was floored
    learns exactly what was downgraded and why.

    Parameters
    ----------
    hub : SynapseHub
        The coordination hub.
    sender : str
        The authenticated identity of the producing agent; stamped as the origin.
    data : dict[str, Any]
        The finding envelope (see
        :meth:`synapse_channel.core.finding.Finding.from_dict`).
    websocket : Any
        The sender's transport, used for a private rejection.
    """
    decision = admit(Finding.from_dict(data))
    if decision.verdict == REJECT or decision.finding is None:
        denied = hub._system(
            "; ".join(decision.reasons) or "finding rejected",
            msg_type=MessageType.FINDING_REJECTED,
            target=sender,
            reasons=list(decision.reasons),
        )
        await hub._send_json(websocket, denied)
        return
    finding = decision.finding
    journal = cast("EventStore", hub.journal)

    def mutate(quota: FindingQuota) -> _MemoryWrite:
        attested = finding.attested(
            by=sender, at=time.time(), project_fallback=sender.split("/", 1)[0]
        )
        quota_ok, quota_message = quota.reserve(sender)
        if not quota_ok:
            return _MemoryWrite(
                accepted=False,
                record=None,
                response=hub._system(
                    quota_message,
                    msg_type=MessageType.FINDING_REJECTED,
                    target=sender,
                    reasons=[quota_message],
                ),
            )
        record = attested.as_dict()
        return _MemoryWrite(
            accepted=True,
            record=record,
            response=hub._system(
                "; ".join(decision.reasons) if decision.reasons else "finding recorded",
                msg_type=MessageType.FINDING_RECORDED,
                verdict=decision.verdict,
                claim_status=attested.claim_status,
                finding=record,
            ),
        )

    def persist(result: _MemoryWrite) -> None:
        if result.accepted:
            record_finding(journal, cast(dict[str, Any], result.record))

    def prepare(result: _MemoryWrite) -> OperationDraft | None:
        if not result.accepted:
            return None
        record = cast(dict[str, Any], result.record)
        return OperationDraft(
            response=result.response,
            events=((EventKind.FINDING, record),),
            intent={"family": "finding", "response_type": MessageType.FINDING_RECORDED},
            response_event_seq_field="seq",
        )

    quota = hub.finding_quota
    try:
        if hub.journal is not None and str(data.get("idem_key") or ""):
            execution = cast(
                AtomicExecution,
                await hub._run_atomic_operation(
                    data,
                    mutate,
                    prepare,
                    subject=quota,
                    publish_candidate=quota.publish_from,
                ),
            )
        else:
            result = await hub.state_mutations.run_subject(
                quota,
                mutate,
                persist=persist if hub.journal is not None else None,
                publish_candidate=quota.publish_from,
            )
            execution = AtomicExecution("uncommitted", result, None)
    except _JOURNAL_FAILURES as exc:
        await _send_journal_failure(hub, websocket, sender=sender, subject="Finding", exc=exc)
        return

    if execution.outcome in {"replayed", "conflict"}:
        await hub._send_json(websocket, _required_response(execution))
        if execution.outcome == "replayed":
            await hub._settle_atomic_operation(data)
        return
    result = cast(_MemoryWrite, execution.mutation)
    if not result.accepted:
        await hub._send_json(websocket, result.response)
        return
    response = execution.response or result.response
    await hub._broadcast(response)
    if execution.outcome == "inserted":
        await hub._settle_atomic_operation(data)
