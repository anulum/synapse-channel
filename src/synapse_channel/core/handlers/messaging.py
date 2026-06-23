# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — base channel messaging handlers (chat relay + heartbeat)
"""Base messaging handlers: the chat relay and the heartbeat keepalive.

Chat is the channel's broadcast primitive: the hub stamps the message with a
sequence id and hub id, retains it in bounded history, journals it when a durable
log is attached, and fans it out to every socket. The heartbeat carries no
payload — the liveness side effect has already been applied by the routing core
before dispatch — so its handler is a deliberate no-op kept in the registry for a
uniform dispatch table.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from synapse_channel.core.journal import record_chat
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


async def handle_chat(hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any) -> None:
    """Stamp, retain, journal, and broadcast a chat message to every socket."""
    data["timestamp"] = float(data.get("timestamp") or time.time())
    data["type"] = MessageType.CHAT
    data["hub_id"] = hub.hub_id
    data["msg_id"] = hub._next_msg_id()
    hub.chat_history.append(data.copy())
    if len(hub.chat_history) > hub.max_history:
        del hub.chat_history[0]
    if hub.journal is not None:
        record_chat(hub.journal, data)
    await hub._broadcast(data)


async def handle_heartbeat(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Acknowledge a keepalive; the liveness update already ran before dispatch."""
