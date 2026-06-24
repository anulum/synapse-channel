# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — persistent-memory write-side handlers (recall query-stream)
"""Write-side handlers feeding an optional persistent-memory layer.

The hub stays memory-agnostic: it carries these records opaquely and never
indexes, ranks, or interprets them — a downstream adapter (e.g. REMANENTIA)
consumes the durable log. What the hub *does* add is the one thing only the
chokepoint every event passes through can: **attestation**. The producing
identity and the receive-time are stamped by the hub, not self-reported, so a
recall log cannot be back-dated or misattributed by its sender.

Recall logging is the query-stream: every lookup the fleet actually makes,
captured so a memory layer can calibrate recall against the *real* query
distribution rather than activity-weighted noise — the measurable prerequisite
for honest, query-weighted recall.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from synapse_channel.core.journal import record_recall
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
