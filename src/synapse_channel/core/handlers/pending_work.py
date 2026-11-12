# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated historical pending work handler
"""Serve only the connected identity's complete bounded recovery metadata."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from synapse_channel.core.pending_work import PendingQuery, read_pending_work
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.event_row_recovery import CorruptEventRow
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.ledger import Blackboard

    class PendingContext(HandlerContext, Protocol):
        """Native state, board and transport capabilities needed by recovery reads."""

        @property
        def blackboard(self) -> Blackboard:
            """Return the indexed native task projection."""
            ...

        @property
        def journal_corrupt_rows(self) -> tuple[CorruptEventRow, ...]:
            """Return retained evidence that startup projection recovery is incomplete."""
            ...


async def handle_pending_query(
    hub: PendingContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Require a durable native projection and refuse mixed or foreign selectors."""
    result: dict[str, object] = {
        "version": 1,
        "available": False,
        "identity": sender,
        "hub_id": hub.hub_id,
        "items": [],
    }
    try:
        query = PendingQuery.from_value(data.get("pending_query"))
        if (
            query.identity != sender
            or hub.journal_corrupt_rows
            or hub.journal is None
            or "activity_query" in data
            or "inbox_query" in data
        ):
            raise ValueError("pending work unavailable")
        result = read_pending_work(
            hub.blackboard, hub.state, query, hub_id=hub.hub_id, now=time.time()
        )
    except ValueError:
        result["error"] = "pending work unavailable"
    request_id = data.get("request_id")
    correlation = (
        {"request_id": request_id}
        if isinstance(request_id, str) and 0 < len(request_id) <= 128
        else {}
    )
    await hub.send_json(
        websocket,
        hub.system(
            "Historical pending work",
            msg_type=MessageType.HISTORY_SNAPSHOT,
            target=sender,
            **correlation,
            pending_work=result,
        ),
    )
