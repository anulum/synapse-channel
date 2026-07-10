# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — client connection accounting for the routing hub
"""Client connection accounting for the routing hub."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.core.hub_counters import HubCounters
from synapse_channel.core.numeric_coercion import safe_float, safe_int
from synapse_channel.core.wake_capability import WAKE_UNKNOWN, normalize_wake_capability

logger = logging.getLogger("synapse.hub")


class HubClientRegistry:
    """Own live socket sets, name bindings, and admission predicates."""

    def __init__(
        self,
        *,
        counters: HubCounters | None = None,
        max_clients: int,
        max_unauth_clients: int | None,
        max_connections_per_host: int | None,
        takeover_cooldown: float,
        clock: Callable[[], float],
        takeover_oscillation_window: float = 30.0,
        takeover_oscillation_threshold: int = 5,
        takeover_quarantine: float = 60.0,
    ) -> None:
        self.max_clients = safe_int(max_clients, default=1, min_value=1)
        self.max_unauth_clients = (
            self.max_clients
            if max_unauth_clients is None
            else safe_int(max_unauth_clients, default=self.max_clients, min_value=1)
        )
        self.max_connections_per_host = (
            None
            if max_connections_per_host is None
            else safe_int(max_connections_per_host, default=1, min_value=1)
        )
        self.takeover_cooldown = max(safe_float(takeover_cooldown, default=0.0), 0.0)
        self.takeover_oscillation_window = max(
            safe_float(takeover_oscillation_window, default=30.0), 0.0
        )
        self.takeover_oscillation_threshold = safe_int(
            takeover_oscillation_threshold, default=5, min_value=2
        )
        self.takeover_quarantine = max(safe_float(takeover_quarantine, default=60.0), 0.0)
        self.counters = counters if counters is not None else HubCounters()
        self._clock = clock
        self._last_takeover: dict[str, float] = {}
        self._takeover_times: dict[str, list[float]] = {}
        self._quarantine_until: dict[str, float] = {}
        self.connected_clients: set[Any] = set()
        self.unauth_clients: set[Any] = set()
        self.agent_sockets: dict[str, Any] = {}
        self.socket_agent: dict[Any, str] = {}
        self.agent_roles: dict[str, tuple[str, ...]] = {}
        self.agent_wake_capabilities: dict[str, str] = {}
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
            self.agent_roles.pop(name, None)
            self.agent_wake_capabilities.pop(name, None)
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

    def set_roles(self, name: str, roles: tuple[str, ...]) -> None:
        """Bind the roles ``name`` answers to, replacing any previous set.

        An empty tuple clears the binding, so a re-register that drops a role removes
        it. Roles are additional ``<project>/<role>`` addresses, not exclusive — a role
        may be held by more than one agent, and a message to it reaches every holder.
        """
        if roles:
            self.agent_roles[name] = roles
        else:
            self.agent_roles.pop(name, None)

    def roles_of(self, name: str) -> tuple[str, ...]:
        """Return the roles ``name`` currently answers to (empty tuple if none)."""
        return self.agent_roles.get(name, ())

    def set_wake_capability(self, name: str, capability: str) -> None:
        """Bind ``name`` to a declared receiver wake capability."""
        normalized = normalize_wake_capability(capability)
        if normalized == WAKE_UNKNOWN:
            self.agent_wake_capabilities.pop(name, None)
        else:
            self.agent_wake_capabilities[name] = normalized

    def wake_capability_of(self, name: str) -> str:
        """Return ``name``'s declared wake capability, or ``unknown`` if absent."""
        return self.agent_wake_capabilities.get(name, WAKE_UNKNOWN)

    def _classify_takeover(self, sender: str, now: float) -> str:
        """Decide a takeover request: ``accept``, ``cooldown``, or quarantine.

        Beyond the short per-name cooldown that merely spaces evictions apart, this
        detects an *oscillation* — two waiters that both claim the same name with
        takeover and so evict each other indefinitely, one per cooldown. Once a name
        is taken over more than ``takeover_oscillation_threshold`` times within
        ``takeover_oscillation_window`` seconds, the name is quarantined for
        ``takeover_quarantine`` seconds: its current owner is pinned and every further
        takeover is refused, which ends the eviction war instead of merely rate-limiting
        it. Returns ``"quarantine_enter"`` the moment quarantine begins (logged once),
        ``"quarantine_active"`` for subsequent refusals while it holds, ``"cooldown"``
        for a too-soon retry, and ``"accept"`` otherwise. Bookkeeping happens here so
        the caller only acts on the verdict.
        """
        until = self._quarantine_until.get(sender)
        if until is not None:
            if now < until:
                return "quarantine_active"
            # quarantine lapsed: forget the history so the name starts fresh
            self._quarantine_until.pop(sender, None)
            self._takeover_times.pop(sender, None)
        last = self._last_takeover.get(sender)
        if last is not None and now - last < self.takeover_cooldown:
            return "cooldown"
        cutoff = now - self.takeover_oscillation_window
        recent = [stamp for stamp in self._takeover_times.get(sender, []) if stamp >= cutoff]
        recent.append(now)
        if len(recent) >= self.takeover_oscillation_threshold:
            self._quarantine_until[sender] = now + self.takeover_quarantine
            self._takeover_times.pop(sender, None)
            self.counters.takeover_quarantines += 1
            return "quarantine_enter"
        self._takeover_times[sender] = recent
        self.counters.takeovers += 1
        return "accept"

    async def resolve_sender(
        self,
        sender: str,
        websocket: Any,
        *,
        takeover: bool,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
    ) -> str | None:
        """Bind a socket to a sender name, enforcing uniqueness and takeover rules.

        Parameters
        ----------
        sender : str
            The name the socket claims.
        websocket : Any
            The socket claiming it.
        takeover : bool
            Whether the claim may evict a current holder of ``sender``.
        send_json : callable
            Coroutine used to deliver the refusal system message on a name
            conflict or a denied name switch.
        system : callable
            Factory for those system message payloads.

        Returns
        -------
        str or None
            The resolved name, or ``None`` when the claim was refused and the
            socket closed.

        Notes
        -----
        An accepted takeover rebinds ``agent_sockets`` and ``socket_agent`` to
        the new owner *synchronously, before* the eviction close handshake
        awaits. From any other task's point of view the name therefore switches
        owner atomically: no interleaving can resolve the name to the evicted
        socket or observe the name unheld mid-takeover.
        """
        known_sender = self.socket_agent.get(websocket)
        if known_sender is None:
            owner_ws = self.agent_sockets.get(sender)
            if owner_ws is not None and owner_ws != websocket:
                if takeover:
                    now = self._clock()
                    verdict = self._classify_takeover(sender, now)
                    if verdict == "quarantine_enter":
                        logger.warning(
                            "takeover quarantine sender=%s requester_host=%s "
                            "reason=oscillation; pinning current owner for %.0fs",
                            sender,
                            self.remote_host(websocket),
                            self.takeover_quarantine,
                        )
                        await self.close_socket(websocket, code=4014, reason="takeover quarantine")
                        return None
                    if verdict == "quarantine_active":
                        await self.close_socket(websocket, code=4014, reason="takeover quarantine")
                        return None
                    if verdict == "cooldown":
                        logger.info(
                            "takeover refused sender=%s requester_host=%s reason=takeover cooldown",
                            sender,
                            self.remote_host(websocket),
                        )
                        await self.close_socket(websocket, code=4014, reason="takeover cooldown")
                        return None
                    self._last_takeover[sender] = now
                    # Swap-then-close: rebind BOTH maps to the new owner before the
                    # close handshake awaits. The eviction used to leave
                    # ``agent_sockets[sender]`` pointing at the dying socket across
                    # that await, so a concurrent directed send resolved a closed
                    # socket and a concurrent takeover of the same name read the
                    # already-evicted owner and co-bound a second live socket.
                    self.socket_agent.pop(owner_ws, None)
                    self.socket_agent[websocket] = sender
                    self.agent_sockets[sender] = websocket
                    logger.info(
                        "takeover accepted sender=%s requester_host=%s previous_host=%s "
                        "reason=superseded",
                        sender,
                        self.remote_host(websocket),
                        self.remote_host(owner_ws),
                    )
                    await self.close_socket(owner_ws, code=4010, reason="superseded")
                    return sender
                logger.info(
                    "name conflict sender=%s requester_host=%s holder_host=%s reason=name conflict",
                    sender,
                    self.remote_host(websocket),
                    self.remote_host(owner_ws),
                )
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
            logger.info(
                "name switch denied original_sender=%s requested_sender=%s remote_host=%s "
                "reason=name switch",
                known_sender,
                sender,
                self.remote_host(websocket),
            )
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
