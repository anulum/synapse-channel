# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP reads from the connected hub's durable inbox
"""Read authenticated journal pages using the bridge's existing connection."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from websockets.exceptions import WebSocketException

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.hub_inbox import inbox_query, validate_inbox_page
from synapse_channel.hub_inbox_cursor import (
    HubInboxCursor,
    hub_inbox_cursor_path,
    load_hub_inbox_cursor,
    save_hub_inbox_cursor,
)

AwaitReply = Callable[
    [Callable[[dict[str, Any]], bool], Callable[[], Awaitable[None]]],
    Awaitable[dict[str, Any] | None],
]


async def drain_hub_inbox(
    agent: SynapseAgent, await_reply: AwaitReply, *, uri: str, home: Path, limit: int
) -> str:
    """Return one validated page from the bridge identity's authenticated hub.

    Parameters
    ----------
    agent : SynapseAgent
        Bridge's already connected client; this call creates no extra socket.
    await_reply : Callable
        Correlated request/reply helper with the bridge's existing timeout.
    uri : str
        Endpoint whose independent local cursor is selected.
    home : Path
        Owner-local coordination home.
    limit : int
        Maximum returned messages, from 1 to 100.

    Returns
    -------
    str
        JSON page or a fixed explicit unavailable result. A refusal never
        consumes an unrelated local feed. Reading does not prove model processing.
    """
    try:
        path = hub_inbox_cursor_path(home, uri, agent.name)
        cursor = load_hub_inbox_cursor(path)
        request_id = uuid.uuid4().hex

        async def send() -> None:
            await agent.send_message(
                MessageType.HISTORY_REQUEST,
                target="System",
                payload="durable inbox",
                request_id=request_id,
                inbox_query=inbox_query(agent.name, cursor, limit),
            )

        response = await await_reply(
            lambda data: (
                data.get("type") == MessageType.HISTORY_SNAPSHOT
                and data.get("request_id") == request_id
            ),
            send,
        )
        if response is None:
            raise ValueError("durable inbox response unavailable")
        page = validate_inbox_page(response, agent.name, cursor, roles=agent.roles)
        if page["hub_id"] != agent.hub_id:
            raise ValueError("inbox page does not belong to the connected hub")
        output = json.dumps(
            {**page, "source": uri, "transport_boundary": "read does not prove model processing"}
        )
        save_hub_inbox_cursor(path, HubInboxCursor(page["hub_id"], page["cursor"]))
        return output
    except (OSError, ValueError, WebSocketException):
        return json.dumps(
            {
                "available": False,
                "identity": agent.name,
                "error": "cannot read durable hub inbox; cursor unchanged",
            }
        )
