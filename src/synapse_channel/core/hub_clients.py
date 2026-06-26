# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — client connection accounting for the routing hub
"""Client connection accounting for the routing hub."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class HubClientRegistry:
    """Own live socket sets, name bindings, and admission predicates."""

    def __init__(
        self,
        *,
        max_clients: int,
        max_unauth_clients: int | None,
        max_connections_per_host: int | None,
        takeover_cooldown: float,
        clock: Callable[[], float],
    ) -> None:
        self.max_clients = max(int(max_clients), 1)
        self.max_unauth_clients = (
            self.max_clients if max_unauth_clients is None else max(int(max_unauth_clients), 1)
        )
        self.max_connections_per_host = (
            None if max_connections_per_host is None else max(int(max_connections_per_host), 1)
        )
        self.takeover_cooldown = max(float(takeover_cooldown), 0.0)
        self._clock = clock
        self._last_takeover: dict[str, float] = {}
        self.connected_clients: set[Any] = set()
        self.unauth_clients: set[Any] = set()
        self.agent_sockets: dict[str, Any] = {}
        self.socket_agent: dict[Any, str] = {}
        self._socket_hosts: dict[Any, str] = {}
        self._host_counts: dict[str, int] = {}

    def at_capacity(self) -> bool:
        """Return whether the total connection table is full."""
        return len(self.connected_clients) >= self.max_clients

    def unauthenticated_at_capacity(self) -> bool:
        """Return whether the pre-authentication connection table is full."""
        return len(self.unauth_clients) >= self.max_unauth_clients

    def host_at_capacity(self, websocket: Any) -> bool:
        """Return whether ``websocket``'s remote host already holds its socket cap."""
        if self.max_connections_per_host is None:
            return False
        return (
            self._host_counts.get(self.remote_host(websocket), 0) >= self.max_connections_per_host
        )

    def add_client(self, websocket: Any) -> None:
        """Record a newly admitted socket."""
        self.connected_clients.add(websocket)
        host = self.remote_host(websocket)
        self._socket_hosts[websocket] = host
        self._host_counts[host] = self._host_counts.get(host, 0) + 1

    def drop_client(self, websocket: Any) -> str | None:
        """Drop a socket and return the active agent name that disappeared, if any."""
        self.connected_clients.discard(websocket)
        host = self._socket_hosts.pop(websocket, None)
        if host is not None:
            remaining = self._host_counts.get(host, 0) - 1
            if remaining > 0:
                self._host_counts[host] = remaining
            else:
                self._host_counts.pop(host, None)
        name = self.socket_agent.pop(websocket, None)
        if name is not None and self.agent_sockets.get(name) == websocket:
            self.agent_sockets.pop(name, None)
            return name
        return None

    def add_unauthenticated(self, websocket: Any) -> None:
        """Record a socket in its secured-hub pre-authentication window."""
        self.unauth_clients.add(websocket)

    def discard_unauthenticated(self, websocket: Any) -> None:
        """Remove a socket from the secured-hub pre-authentication window."""
        self.unauth_clients.discard(websocket)

    def is_bound(self, websocket: Any) -> bool:
        """Return whether the socket has already bound an agent name."""
        return websocket in self.socket_agent

    def bound_agent(self, websocket: Any) -> str | None:
        """Return the agent name bound to the socket, if any."""
        return self.socket_agent.get(websocket)

    def set_agent_socket(self, sender: str, websocket: Any) -> bool:
        """Bind ``sender`` to ``websocket`` and return whether it was newly online."""
        is_new_agent = sender not in self.agent_sockets
        self.agent_sockets[sender] = websocket
        return is_new_agent

    async def resolve_sender(
        self,
        sender: str,
        websocket: Any,
        *,
        takeover: bool,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
    ) -> str | None:
        """Bind a socket to a sender name, enforcing uniqueness and takeover rules."""
        known_sender = self.socket_agent.get(websocket)
        if known_sender is None:
            owner_ws = self.agent_sockets.get(sender)
            if owner_ws is not None and owner_ws != websocket:
                if takeover:
                    now = self._clock()
                    last = self._last_takeover.get(sender)
                    if last is not None and now - last < self.takeover_cooldown:
                        await self.close_socket(websocket, code=4014, reason="takeover cooldown")
                        return None
                    self._last_takeover[sender] = now
                    self.socket_agent.pop(owner_ws, None)
                    await self.close_socket(owner_ws, code=4010, reason="superseded")
                    self.socket_agent[websocket] = sender
                    return sender
                await send_json(
                    websocket,
                    system(
                        f"Name '{sender}' is already online from another session. "
                        "Use a unique --name.",
                        msg_type="name_conflict",
                        target=sender,
                    ),
                )
                await self.close_socket(websocket, code=4009, reason="name conflict")
                return None
            self.socket_agent[websocket] = sender
            return sender
        if known_sender != sender:
            await send_json(
                websocket,
                system(
                    f"Sender name switch denied: '{known_sender}' -> '{sender}'. "
                    "Reconnect with a new --name.",
                    msg_type="name_conflict",
                    target=known_sender,
                ),
            )
            await self.close_socket(websocket, code=4009, reason="name switch")
            return None
        return known_sender

    @staticmethod
    async def close_socket(websocket: Any, *, code: int, reason: str) -> None:
        """Close a websocket and wait for close propagation when supported."""
        try:
            await websocket.close(code=code, reason=reason)
            wait_closed = getattr(websocket, "wait_closed", None)
            if callable(wait_closed):
                await wait_closed()
        # Closing is best-effort: the socket may already be gone.
        except Exception:  # nosec B110
            pass

    @staticmethod
    def remote_host(websocket: Any) -> str:
        """Return the remote host of ``websocket`` for per-host rate keying."""
        address = getattr(websocket, "remote_address", None)
        if isinstance(address, (tuple, list)) and address:
            return str(address[0])
        return str(address) if address else "unknown"
