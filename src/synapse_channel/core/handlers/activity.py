# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated durable activity query handler
"""Serve only the admitted connection's exact durable activity metadata."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from synapse_channel.core.activity_pages import ActivityQuery, read_activity_page
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.handler_context import HandlerContext


async def handle_activity_query(
    hub: HandlerContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Reply privately after exact connection ownership and durable cursor checks."""
    page: dict[str, object] = {
        "version": 1,
        "available": False,
        "identity": sender,
        "hub_id": hub.hub_id,
        "cursor": 0,
        "items": [],
        "has_more": False,
    }
    try:
        query = ActivityQuery.from_value(data.get("activity_query"))
        if "inbox_query" in data or query.identity != sender or hub.journal is None:
            raise ValueError("activity query is not available")
        page = read_activity_page(hub.journal, query, hub_id=hub.hub_id)
    except (ValueError, OSError, sqlite3.Error):
        page["error"] = "activity page unavailable"
    request_id = data.get("request_id")
    correlation = (
        {"request_id": request_id}
        if isinstance(request_id, str) and 0 < len(request_id) <= 128
        else {}
    )
    await hub.send_json(
        websocket,
        hub.system(
            "Durable activity page",
            msg_type=MessageType.HISTORY_SNAPSHOT,
            target=sender,
            **correlation,
            activity_page=page,
        ),
    )
