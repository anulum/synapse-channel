# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only snapshot handlers (state/who/history/resume/board/manifest)
"""Read-only snapshot handlers.

Each function answers a request by sending one private snapshot back to the
asking socket and mutating nothing: the lease/resource state, the online roster,
recent or cursor-bounded chat history, the shared plan board, or the capability
manifest. They share the routing signature so the dispatch table treats them
uniformly; the read handlers that need no request body simply ignore ``data``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


async def handle_state_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent a full state snapshot."""
    await hub._send_json(
        websocket,
        hub._system(
            "State snapshot",
            msg_type=MessageType.STATE_SNAPSHOT,
            target=sender,
            snapshot=hub.state.snapshot(),
        ),
    )


async def handle_who_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent the online-agent roster."""
    await hub._send_json(
        websocket,
        hub._system(
            "Who snapshot",
            msg_type=MessageType.WHO_SNAPSHOT,
            target=sender,
            online_agents=hub.online_agents(),
            connected_clients=len(hub.connected_clients),
        ),
    )


async def handle_history_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent recent (or full) chat history."""
    raw_limit = data.get("limit")
    limit: int | None
    if raw_limit is None:
        limit = None
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = None
    if limit is None:
        history = list(hub.chat_history)
        requested_limit: int | str = "all"
    else:
        n = max(1, limit)
        history = list(hub.chat_history)[-n:]
        requested_limit = n
    await hub._send_json(
        websocket,
        hub._system(
            "History snapshot",
            msg_type=MessageType.HISTORY_SNAPSHOT,
            target=sender,
            history=history,
            requested_limit=requested_limit,
        ),
    )


async def handle_resume_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent every chat message after a cursor.

    Lets a reconnected agent catch up on exactly the messages it missed,
    identified by the ``since`` chat ``msg_id`` it last saw, rather than
    pulling a fixed-size history window.

    Parameters
    ----------
    hub : SynapseHub
        The hub whose chat history and transport the handler uses.
    sender : str
        The requesting agent.
    data : dict[str, Any]
        The request; ``since`` is the last ``msg_id`` the agent has seen.
    websocket : Any
        The requesting socket.
    """
    raw_since = data.get("since", 0)
    try:
        since = int(raw_since)
    except (TypeError, ValueError):
        since = 0
    tail = [m for m in hub.chat_history if int(m.get("msg_id", 0)) > since]
    await hub._send_json(
        websocket,
        hub._system(
            "Resume snapshot",
            msg_type=MessageType.RESUME_SNAPSHOT,
            target=sender,
            since=since,
            messages=tail,
        ),
    )


async def handle_board_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent a snapshot of the shared blackboard."""
    await hub._send_json(
        websocket,
        hub._system(
            "Board snapshot",
            msg_type=MessageType.BOARD_SNAPSHOT,
            target=sender,
            board=hub.blackboard.snapshot(),
        ),
    )


async def handle_manifest_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent the capability manifest."""
    await hub._send_json(
        websocket,
        hub._system(
            "Manifest snapshot",
            msg_type=MessageType.MANIFEST_SNAPSHOT,
            target=sender,
            manifest=hub.capabilities.manifest(),
        ),
    )
