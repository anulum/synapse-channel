# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private-channel membership handlers
"""Hub handlers for private-channel create/join/leave/list operations.

Each handler mutates the hub's :class:`~synapse_channel.core.channels.ChannelRegistry`
and replies privately to the requesting socket. Membership is the routing
audience for channel-scoped chat (see ``handle_chat``); these handlers never
broadcast a channel body, only a private acknowledgement to the requester.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


async def _reply(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    channel: str,
    ok: bool,
    message: str,
) -> None:
    """Send a private channel-operation result to the requester.

    The member roster is disclosed only when the operation succeeded *and* the
    requester is a current member afterwards, so a failed create/leave never
    leaks the membership of a channel the requester does not belong to (otherwise
    a non-member could probe arbitrary ids and harvest rosters — an enumeration
    oracle).
    """
    members = (
        sorted(hub.channels.members(channel))
        if ok and hub.channels.is_member(channel, sender)
        else []
    )
    await hub._send_json(
        websocket,
        hub._system(
            message,
            msg_type=MessageType.CHANNEL_RESULT,
            target=sender,
            channel=channel,
            ok=ok,
            members=members,
        ),
    )


async def handle_channel_create(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Create a private channel owned by the requester."""
    channel = str(data.get("channel") or "").strip()
    label = str(data.get("label") or "")
    ok, message = hub.channels.create(channel, owner=sender, label=label)
    await _reply(hub, websocket, sender=sender, channel=channel, ok=ok, message=message)


async def handle_channel_join(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Join the requester to a private channel."""
    channel = str(data.get("channel") or "").strip()
    ok, message = hub.channels.join(channel, sender)
    await _reply(hub, websocket, sender=sender, channel=channel, ok=ok, message=message)


async def handle_channel_leave(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Remove the requester from a private channel."""
    channel = str(data.get("channel") or "").strip()
    ok, message = hub.channels.leave(channel, sender)
    await _reply(hub, websocket, sender=sender, channel=channel, ok=ok, message=message)


async def handle_channel_list_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Return the channels the requester is a member of."""
    del data
    await hub._send_json(
        websocket,
        hub._system(
            "channel list",
            msg_type=MessageType.CHANNEL_LIST,
            target=sender,
            channels=hub.channels.channels_for(sender),
        ),
    )


async def handle_channel_history_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Return retained channel history visible to the requester."""
    channel = str(data.get("channel") or "").strip()
    # A non-numeric or overflowing limit falls back to the default; negatives clamp to 0.
    limit = safe_int(data.get("limit", 20), default=20, min_value=0)
    if not hub.channels.is_member(channel, sender):
        await _reply(
            hub,
            websocket,
            sender=sender,
            channel=channel,
            ok=False,
            message=f"not a member of channel '{channel}'",
        )
        return
    await hub._send_json(
        websocket,
        hub._system(
            "channel history",
            msg_type=MessageType.CHANNEL_HISTORY,
            target=sender,
            channel=channel,
            messages=hub.channels.history_for(channel, sender, limit=limit),
            retention={"max_messages": hub.max_history},
        ),
    )
