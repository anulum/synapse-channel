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
from collections.abc import Callable, Iterable
from typing import Any

from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_relay import RelayMirror
from synapse_channel.core.outbound_send import OutboundSender
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
        The hub's system-message factory (``hub.system``), used to stamp a presence
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
        self._sender = OutboundSender()

    async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        """Serialise and send one message to a single socket."""
        await self._sender.send(websocket, json.dumps(data))

    async def broadcast(self, data: dict[str, Any]) -> frozenset[str]:
        """Mirror and fan out one frame, returning successful bound socket names.

        The message is mirrored to the relay log first — even with no socket
        connected — so the log captures it for a later observer.
        """
        await self._relay.mirror_async(data)
        clients = tuple(self._clients.connected_clients)
        owners = {socket: self._clients.socket_agent.get(socket) for socket in clients}
        if not clients:
            return frozenset()
        raw = json.dumps(data)
        results = await asyncio.gather(
            *(self._sender.send(client, raw) for client in clients),
            return_exceptions=True,
        )
        return frozenset(
            name
            for socket, result in zip(clients, results, strict=True)
            if result is None and (name := owners[socket]) is not None
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
        """Send to a named agent's socket; return whether the send succeeded.

        A recipient can vanish between recipient resolution and this send — a
        channel member that disconnects mid fan-out leaves no live socket, and
        a socket that died before the hub pruned its binding fails the send —
        so both misses are reported as ``False``, never raised.
        """
        websocket = self._clients.agent_sockets.get(agent)
        if websocket is None:
            return False
        try:
            await self.send_json(websocket, data)
            return True
        except Exception:
            return False

    async def send_directed(
        self, data: dict[str, Any], *, names: Iterable[str], sender_socket: Any = None
    ) -> frozenset[str]:
        """Send one directed message to a named audience only, never the whole hub.

        The message is mirrored to the relay log first — exactly as
        :meth:`broadcast` does — so the durable feed still captures every directed
        message for the journal, a feeds-backed dashboard, and the federation
        follower; only the *live socket* fan-out is narrowed. It is delivered to the
        socket of each name in ``names`` that is online (recipients, their ``-rx``
        waiter sidecars, and any granted observers, resolved by the caller) and, when
        given, back to ``sender_socket`` so the sender still sees its own message.
        Each socket is sent at most once, and a name with no live socket is skipped.
        """
        await self._relay.mirror_async(data)
        sockets: set[Any] = set()
        if sender_socket is not None:
            sockets.add(sender_socket)
        owners: dict[Any, set[str]] = {}
        for name in names:
            websocket = self._clients.agent_sockets.get(name)
            if websocket is not None:
                sockets.add(websocket)
                owners.setdefault(websocket, set()).add(name)
        if not sockets:
            return frozenset()
        raw = json.dumps(data)
        audience = tuple(sockets)
        results = await asyncio.gather(
            *(self._sender.send(socket, raw) for socket in audience), return_exceptions=True
        )
        return frozenset(
            name
            for socket, result in zip(audience, results, strict=True)
            if result is None
            for name in owners.get(socket, ())
        )
