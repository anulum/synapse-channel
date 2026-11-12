# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — repeatable exact-recipient MCP journal pages
"""Read one native journal page without consuming any server-local cursor."""

from __future__ import annotations

import json
import uuid

from websockets.exceptions import WebSocketException

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.hub_inbox import inbox_query, validate_inbox_page
from synapse_channel.hub_inbox_cursor import HubInboxCursor
from synapse_channel.mcp.hub_inbox import AwaitReply


async def read_inbox_page(
    agent: SynapseAgent,
    await_reply: AwaitReply,
    *,
    hub_id: str = "",
    after_seq: int = 0,
    limit: int = 50,
) -> str:
    """Read exact directed messages using a caller-owned, hub-bound cursor.

    Parameters
    ----------
    agent : SynapseAgent
        Admitted client whose exact identity is fixed by the bridge.
    await_reply : AwaitReply
        Existing correlated exchange, with its finite response deadline.
    hub_id : str, optional
        Hub identifier from the last accepted page; empty only for an initial read.
    after_seq : int, optional
        Last accepted scanned sequence, from zero through the signed 64-bit maximum.
    limit : int, optional
        Native page size, from one to one hundred. Filtering can return fewer rows.

    Returns
    -------
    str
        Validated JSON page. No local cursor or processing receipt is written.
        Preserve the supplied cursor until this response is accepted; repeat it
        after a lost reply. Empty pages may still have ``has_more`` set because
        broadcast, group and multi-recipient rows are deliberately omitted.

    Raises
    ------
    ValueError
        When a bound is invalid or a noninitial cursor lacks its hub identifier.
    RuntimeError
        When the connected hub cannot confirm a valid page for this identity.
    """
    if (
        type(hub_id) is not str
        or len(hub_id) > 256
        or type(after_seq) is not int
        or not 0 <= after_seq <= 9223372036854775807
        or (after_seq != 0 and not hub_id)
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise ValueError("invalid durable inbox page cursor or limit")
    cursor = HubInboxCursor(hub_id, after_seq)
    request_id = uuid.uuid4().hex

    async def send() -> None:
        await agent.send_message(
            MessageType.HISTORY_REQUEST,
            target="System",
            payload="durable inbox",
            request_id=request_id,
            inbox_query=inbox_query(agent.name, cursor, limit),
        )

    try:
        response = await await_reply(
            lambda frame: (
                frame.get("type") == MessageType.HISTORY_SNAPSHOT
                and frame.get("request_id") == request_id
            ),
            send,
        )
        if response is None:
            raise ValueError("durable inbox response unavailable")
        page = validate_inbox_page(response, agent.name, cursor, roles=agent.roles)
        if page["hub_id"] != agent.hub_id or len(page["messages"]) > limit:
            raise ValueError("durable inbox response binding or size mismatch")
        return json.dumps(
            {
                **page,
                "messages": [row for row in page["messages"] if row.get("target") == agent.name],
                "source": "hub",
                "transport_boundary": "read does not prove model processing",
            }
        )
    except (OSError, ValueError, WebSocketException):
        raise RuntimeError("durable inbox page unavailable; caller cursor unchanged") from None
