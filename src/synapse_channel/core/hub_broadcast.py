# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fan out hub messages to sockets and named agents
"""Outbound messaging for the routing hub.

:class:`HubBroadcaster` owns how a message leaves the hub: serialising one frame to
a single socket, fanning a broadcast out to every connected client (mirroring it to
the relay log first), addressing one named agent, and composing a presence update.
It reads the live socket registry rather than capturing it, mirrors through the
:class:`~synapse_channel.core.hub_relay.RelayMirror`, and takes the hub's system-message
factory and online-agents roster as injected callbacks, so it carries no back-reference
to the hub — the same callback-injection the client registry uses for sender resolution.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_relay import RelayMirror
from synapse_channel.core.protocol import MessageType


class HubBroadcaster:
    """Send hub messages to single sockets, every client, or a named agent.

    Parameters
    ----------
    clients : HubClientRegistry
        The live socket registry; ``connected_clients`` and ``agent_sockets`` are
        read fresh on each send so membership changes are always reflected.
    relay : RelayMirror
        Mirror every broadcast is written to before it fans out, so a disconnected
        observer can catch up from the file later.
    system : Callable[..., dict]
        The hub's system-message factory (``hub._system``), used to stamp a presence
        update with the hub id.
    online_agents : Callable[[], list[str]]
        Returns the current roster of registered agent names for the presence update.
    """

    def __init__(
        self,
        clients: HubClientRegistry,
        relay: RelayMirror,
        *,
        system: Callable[..., dict[str, Any]],
        online_agents: Callable[[], list[str]],
    ) -> None:
        self._clients = clients
        self._relay = relay
        self._system = system
        self._online_agents = online_agents

    async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        """Serialise and send one message to a single socket."""
        await websocket.send(json.dumps(data))

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Send one message to every connected socket, ignoring failures.

        The message is mirrored to the relay log first — even with no socket
        connected — so the log captures it for a later observer.
        """
        self._relay.mirror(data)
        clients = self._clients.connected_clients
        if not clients:
            return
        raw = json.dumps(data)
        await asyncio.gather(
            *(client.send(raw) for client in clients),
            return_exceptions=True,
        )

    async def broadcast_presence(self, event: str, agent: str | None = None) -> None:
        """Broadcast a presence update naming who joined or left."""
        await self.broadcast(
            self._system(
                "Presence update",
                msg_type=MessageType.PRESENCE_UPDATE,
                online_agents=self._online_agents(),
                event=event,
                agent=agent,
            )
        )

    async def send_to_agent(self, agent: str, data: dict[str, Any]) -> bool:
        """Send to a named agent's socket; return whether the send succeeded."""
        websocket = self._clients.agent_sockets.get(agent)
        if websocket is None:  # pragma: no cover - public routing binds senders before use.
            return False
        try:
            await self.send_json(websocket, data)
            return True
        except Exception:  # pragma: no cover - defensive half-closed socket guard.
            return False
