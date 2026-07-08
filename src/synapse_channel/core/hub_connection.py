# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serve one socket from connect through authentication to teardown
"""Socket-connection lifecycle for the routing hub.

:class:`HubConnection` owns a client socket from the moment it connects to the
moment it is dropped: admitting it against the capacity, per-host, and
unauthenticated-burst ceilings; welcoming it (immediately on an open hub, or only
after the first frame authenticates on a secured one); reading the authenticated
first frame under the auth deadline; pumping subsequent frames into the routing
pipeline; and, on disconnect, releasing the agent name and broadcasting the
departure. It also wires ``SIGTERM``/``SIGINT`` to a graceful-shutdown event.

Frame routing itself stays on the hub: :class:`HubConnection` receives the hub's
:meth:`~synapse_channel.core.hub.SynapseHub.handle_message` — and the presence
broadcast, per-socket send, system-message factory, online roster, and wait-graph
pruning — as injected callbacks, so it carries no back-reference to the hub, the
same callback-injection :class:`~synapse_channel.core.hub_broadcast.HubBroadcaster`
uses. The authenticator, auth timeout, and rate limiter are captured at
construction because the hub never mutates them after ``__init__``; the connect and
disconnect notices are logged through a logger named ``synapse.hub`` so their
records stay under the hub's log namespace.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.exceptions import ConnectionClosed

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability import CapabilityRegistry
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION, MessageType
from synapse_channel.core.ratelimit import RateLimiter

logger = logging.getLogger("synapse.hub")


class HubConnection:
    """Serve one client socket from admission through authentication to teardown.

    Parameters
    ----------
    clients : HubClientRegistry
        The live socket registry: admission ceilings, name binding, the
        unauthenticated set, and the connection roster are all read and mutated
        through it.
    capabilities : CapabilityRegistry
        The per-agent capability advertisements, cleared for an agent when its
        socket disconnects.
    authenticator : TokenAuthenticator or None
        When set, the hub is secured: a socket is welcomed only after its first
        frame authenticates, and an unauthenticated-burst ceiling applies. ``None``
        leaves the hub open, so a socket is welcomed on connect.
    auth_timeout : float
        Seconds a secured hub waits for an authenticated first frame before closing
        an idle socket (already clamped by the hub).
    rate_limiter : RateLimiter or None
        The per-agent frame limiter, whose bucket for an agent is forgotten when the
        agent disconnects; ``None`` when rate limiting is disabled.
    handle_message : Callable[[str | bytes, Any], Awaitable[None]]
        The hub's frame router (``hub.handle_message``); the first authenticated
        frame and every subsequent frame are pumped through it.
    send_json : Callable[[Any, dict], Awaitable[None]]
        The hub's per-socket send (``hub._send_json``), used to deliver the welcome.
    system : Callable[..., dict]
        The hub's system-message factory (``hub._system``), used to stamp the
        welcome with the hub id.
    online_agents : Callable[[], list[str]]
        Returns the current roster of registered agent names for the welcome.
    broadcast_presence : Callable[[str, str | None], Awaitable[None]]
        The hub's presence broadcast (``hub._broadcast_presence``), used to announce
        a departure when a named socket disconnects.
    drop_waits : Callable[[str], None]
        The hub's wait-graph pruning (``hub._drop_waits``), used to remove a
        departing agent's wait edges.
    forget_liveness : Callable[[str], None]
        The hub's recipient-liveness pruning (``hub._recipient_liveness.forget``),
        used to drop a departing agent's last-reaction record so the store stays
        bounded to currently connected identities.
    """

    def __init__(
        self,
        clients: HubClientRegistry,
        capabilities: CapabilityRegistry,
        *,
        authenticator: TokenAuthenticator | None,
        auth_timeout: float,
        rate_limiter: RateLimiter | None,
        handle_message: Callable[[str | bytes, Any], Awaitable[None]],
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
        online_agents: Callable[[], list[str]],
        broadcast_presence: Callable[[str, str | None], Awaitable[None]],
        drop_waits: Callable[[str], None],
        forget_liveness: Callable[[str], None],
    ) -> None:
        self._clients = clients
        self._capabilities = capabilities
        self._authenticator = authenticator
        self._auth_timeout = auth_timeout
        self._rate_limiter = rate_limiter
        self._handle_message = handle_message
        self._send_json = send_json
        self._system = system
        self._online_agents = online_agents
        self._broadcast_presence = broadcast_presence
        self._drop_waits = drop_waits
        self._forget_liveness = forget_liveness

    async def register(self, websocket: Any) -> None:
        """Record a new socket; welcome it now only on an open hub.

        On a secured hub the welcome — which carries the online roster and the
        connection count — is withheld until the socket authenticates (see
        :meth:`~synapse_channel.core.hub.SynapseHub.handle_message`), so an
        unauthenticated client never learns who is online. An open hub has nothing
        to gate, so it is welcomed on connect.
        """
        self._clients.add_client(websocket)
        logger.info(
            "Client connected: %s (total=%d)",
            id(websocket),
            len(self._clients.connected_clients),
        )
        if self._authenticator is None:
            await self.send_welcome(websocket)

    async def unregister(self, websocket: Any) -> None:
        """Drop a socket, releasing its agent name and broadcasting departure."""
        name = self._clients.drop_client(websocket)
        if name is not None:
            self._drop_waits(name)
            self._capabilities.forget(name)
            self._forget_liveness(name)
            if self._rate_limiter is not None:
                self._rate_limiter.forget(name)
            await self._broadcast_presence("left", name)
        logger.info(
            "Client disconnected: %s (total=%d)",
            id(websocket),
            len(self._clients.connected_clients),
        )

    async def send_welcome(self, websocket: Any) -> None:
        """Send the welcome frame (roster, connection count, and wire version) to one socket."""
        await self._send_json(
            websocket,
            self._system(
                "Welcome to Synapse",
                msg_type=MessageType.WELCOME,
                target="self",
                connected_clients=len(self._clients.connected_clients),
                online_agents=self._online_agents(),
                protocol_version=WIRE_PROTOCOL_VERSION,
            ),
        )

    async def authenticate_or_close(self, websocket: Any) -> bool:
        """On a secured hub, process the first frame under the auth deadline.

        Reads one frame within ``auth_timeout``, routes it (which authenticates
        and binds the sender, then sends the withheld welcome), and reports whether
        the socket is now an authenticated, bound client. A socket that sends
        nothing in time is closed (``4012``) so an idle unauthenticated connection
        cannot hold a slot; a first frame that fails to authenticate or bind is
        closed (``4010``).

        Returns
        -------
        bool
            ``True`` when the socket authenticated and bound a name, ``False``
            when it timed out, disconnected, or failed to authenticate (the socket
            is closed in every ``False`` case).
        """
        try:
            first = await asyncio.wait_for(websocket.recv(), timeout=self._auth_timeout)
        except asyncio.TimeoutError:
            await websocket.close(code=4012, reason="auth timeout")
            return False
        except ConnectionClosed:
            return False
        await self._handle_message(first, websocket)
        if not self._clients.is_bound(websocket):
            # The first frame did not authenticate and bind a name; the token gate
            # may already have closed the socket, so closing again is suppressed.
            with contextlib.suppress(Exception):
                await websocket.close(code=4010, reason="auth required")
            return False
        return True

    async def handler(self, websocket: Any) -> None:
        """Serve one client connection from registration to disconnect.

        On a secured hub the first frame must authenticate within ``auth_timeout``
        before the connection joins the channel (see :meth:`authenticate_or_close`).
        A separate unauthenticated-burst cap refuses a new socket (code ``4014``)
        while that many sockets are still in their pre-auth window, so an
        authentication-stall burst cannot occupy the connection table for the whole
        timeout.
        """
        if self._clients.at_capacity():
            await websocket.close(code=4013, reason="hub at capacity")
            return
        if self._clients.host_at_capacity(websocket):
            await websocket.close(code=4015, reason="too many connections from host")
            return
        if self._authenticator is not None and self._clients.unauthenticated_at_capacity():
            await websocket.close(code=4014, reason="too many unauthenticated connections")
            return
        await self.register(websocket)
        try:
            if self._authenticator is not None:
                self._clients.add_unauthenticated(websocket)
                try:
                    authenticated = await self.authenticate_or_close(websocket)
                finally:
                    self._clients.discard_unauthenticated(websocket)
                if not authenticated:
                    return
            async for raw in websocket:
                await self._handle_message(raw, websocket)
        except ConnectionClosed:
            pass
        finally:
            await self.unregister(websocket)

    @staticmethod
    def install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
        """Wire ``SIGTERM``/``SIGINT`` to set ``stop`` for a graceful shutdown.

        Best-effort: a platform without signal support (e.g. the Windows proactor
        loop) raises ``NotImplementedError``, which is suppressed — the hub then
        simply runs until its task is cancelled.
        """
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
