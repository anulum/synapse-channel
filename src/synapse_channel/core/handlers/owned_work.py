# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated current-owned work handler
"""Serve exact-owner business work through a separately authorised native query."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from synapse_channel.core.owned_pages import read_owned_work
from synapse_channel.core.owned_work import OwnedQuery
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.handlers.pending_work import PendingContext


async def handle_owned_work_query(
    hub: PendingContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Refuse incomplete projections, mixed selectors and foreign connected owners."""
    result: dict[str, object] = {
        "version": 1,
        "available": False,
        "identity": sender,
        "hub_id": hub.hub_id,
        "error": "owned work unavailable",
    }
    try:
        query = OwnedQuery.from_value(data.get("owned_work_query"))
        if (
            query.identity != sender
            or hub.journal is None
            or hub.journal_corrupt_rows
            or any(k in data for k in ("pending_query", "activity_query", "inbox_query"))
        ):
            raise ValueError("owned work unavailable")
        result = read_owned_work(
            hub.blackboard, hub.state, query, hub_id=hub.hub_id, now=time.time()
        )
    except ValueError:
        pass
    request_id = data.get("request_id")
    correlation = (
        {"request_id": request_id}
        if (isinstance(request_id, str) and 0 < len(request_id) <= 128)
        else {}
    )
    await hub.send_json(
        websocket,
        hub.system(
            "Current owned work",
            msg_type=MessageType.HISTORY_SNAPSHOT,
            target=sender,
            **correlation,
            owned_work=result,
        ),
    )
