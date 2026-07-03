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

from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.journal import record_chat
from synapse_channel.core.protocol import MessageType, is_recipient

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


async def handle_chat(hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any) -> None:
    """Stamp, retain, journal, and broadcast a chat message to every socket.

    A message carrying a ``channel`` is audience-scoped instead: it is delivered
    only to that channel's online members and never broadcast, retained in the
    public history, or mirrored to the relay log.
    """
    data["timestamp"] = float(data.get("timestamp") or time.time())
    data["type"] = MessageType.CHAT
    data["hub_id"] = hub.hub_id
    data["msg_id"] = hub._next_msg_id()
    channel = str(data.get("channel") or "").strip()
    if channel:
        await _route_channel_chat(hub, sender, data, websocket, channel)
        return
    target = str(data.get("target") or "all")
    recipients = _matching_online_recipients(target, sender, hub.online_agents())
    if is_directed_target(target):
        hub.counters.chat_directed += 1
    else:
        hub.counters.chat_broadcast += 1
    if not recipients and is_directed_target(target):
        # durable but waking nobody - remember the blackhole so the state
        # snapshot can show it instead of a human discovering it by relaying
        hub.dead_letters.record(target, sender=sender, ts=float(data["timestamp"]))
    hub.chat_history.append(data.copy())
    if len(hub.chat_history) > hub.max_history:
        del hub.chat_history[0]
    if hub.journal is not None:
        record_chat(hub.journal, data)
    await hub._broadcast(data)
    if bool(data.get("receipt_requested")):
        await _send_delivery_receipt(
            hub,
            websocket,
            sender=sender,
            target=target,
            msg_id=int(data["msg_id"]),
            recipients=recipients,
        )


async def _route_channel_chat(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any, channel: str
) -> None:
    """Deliver a channel-scoped chat to online members only, never broadcast.

    Non-members are refused privately. The body is not retained in the public
    chat history, but it is retained in the channel's bounded live history and
    mirrored/journalled with explicit channel metadata so relay and event-query
    can filter it.
    """
    if not hub.channels.is_member(channel, sender):
        await hub._send_json(
            websocket,
            hub._system(
                f"not a member of channel '{channel}'",
                msg_type=MessageType.ERROR,
                target=sender,
                channel=channel,
            ),
        )
        return
    online = set(hub.online_agents())
    recipients = sorted(
        member for member in hub.channels.members(channel) if member != sender and member in online
    )
    hub.channels.retain_message(channel, data, max_messages=hub.max_history)
    if hub.journal is not None:
        record_chat(hub.journal, data)
    hub._mirror_to_relay(data)
    for member in recipients:
        await hub._send_to_agent(member, data)
    if bool(data.get("receipt_requested")):
        await _send_delivery_receipt(
            hub,
            websocket,
            sender=sender,
            target=channel,
            msg_id=int(data["msg_id"]),
            recipients=recipients,
        )


def _matching_online_recipients(target: str, sender: str, online_agents: list[str]) -> list[str]:
    """Return online recipients reached by ``target``, excluding the sender socket."""
    return sorted(name for name in online_agents if name != sender and is_recipient(target, name))


async def _send_delivery_receipt(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    target: str,
    msg_id: int,
    recipients: list[str],
) -> None:
    """Send a private delivery receipt for a receipt-requested chat."""
    delivered = bool(recipients)
    if delivered:
        payload = f"delivered to {', '.join(recipients)}"
    else:
        payload = f"delivery failed: no online recipient matched {target}"
    await hub._send_json(
        websocket,
        hub._system(
            payload,
            msg_type=MessageType.DELIVERY_RECEIPT,
            target=sender,
            message_target=target,
            message_id=msg_id,
            delivered=delivered,
            recipients=recipients,
        ),
    )


async def handle_heartbeat(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Acknowledge a keepalive; the liveness update already ran before dispatch."""
