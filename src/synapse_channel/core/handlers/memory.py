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

import time
from typing import TYPE_CHECKING, Any

from synapse_channel.core.emit_gate import REJECT, admit
from synapse_channel.core.finding import Finding
from synapse_channel.core.journal import record_finding, record_recall
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


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
    record = {
        "query_text": str(data.get("query_text") or ""),
        "returned_claim_ids": returned,
        "was_used": bool(data.get("was_used", False)),
        "abstained": bool(data.get("abstained", False)),
        "by": sender,
        "at": time.time(),
    }
    if hub.journal is not None:
        record_recall(hub.journal, record)
    ack = hub._system(
        "recall logged",
        msg_type=MessageType.RECALL_LOGGED,
        target=sender,
    )
    hub._remember(data, ack)
    await hub._send_json(websocket, ack)


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
        hub._remember(data, denied)
        await hub._send_json(websocket, denied)
        return

    attested = decision.finding.attested(
        by=sender, at=time.time(), project_fallback=sender.split("/", 1)[0]
    )
    record = attested.as_dict()
    quota_ok, quota_message = hub.reserve_finding_slot(sender)
    if not quota_ok:
        denied = hub._system(
            quota_message,
            msg_type=MessageType.FINDING_REJECTED,
            target=sender,
            reasons=[quota_message],
        )
        hub._remember(data, denied)
        await hub._send_json(websocket, denied)
        return
    if hub.journal is not None:
        record_finding(hub.journal, record)
    recorded = hub._system(
        "; ".join(decision.reasons) if decision.reasons else "finding recorded",
        msg_type=MessageType.FINDING_RECORDED,
        verdict=decision.verdict,
        claim_status=attested.claim_status,
        finding=record,
    )
    hub._remember(data, recorded)
    await hub._broadcast(recorded)
