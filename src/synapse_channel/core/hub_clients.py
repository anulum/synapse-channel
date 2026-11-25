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

from synapse_channel.core.agent_session_attributes import (
    AgentSessionAttributes,
)
from synapse_channel.core.agent_session_attributes import (
    DeliverySession as DeliverySession,
)
from synapse_channel.core.connection_capacity import ConnectionCapacity
from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.hub_address import is_hub_qualified_name
from synapse_channel.core.hub_counters import HubCounters
from synapse_channel.core.name_ownership import DEFAULT_LEASE_OFFLINE_TTL, NameOwnership
from synapse_channel.core.protocol import SENDER_HUB, MessageType
from synapse_channel.core.role_mailbox import is_role_mailbox_key
from synapse_channel.core.takeover_policy import TakeoverPolicy

logger = logging.getLogger("synapse.hub")

_RESERVED_AGENT_NAMES = frozenset({SENDER_HUB.casefold(), "synapse", "system"})
"""Global names reserved for hub and protocol provenance."""


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
        lease_offline_ttl: float = DEFAULT_LEASE_OFFLINE_TTL,
        secured: bool = False,
    ) -> None:
        self.counters = counters if counters is not None else HubCounters()
        self._clock = clock
        self._secured = secured
        self._capacity = ConnectionCapacity(
            max_clients=max_clients,
            max_unauth_clients=max_unauth_clients,
            max_connections_per_host=max_connections_per_host,
            remote_host=HubClientRegistry.remote_host,
        )
        self._takeover = TakeoverPolicy(
            cooldown=takeover_cooldown,
            window=takeover_oscillation_window,
            threshold=takeover_oscillation_threshold,
            quarantine=takeover_quarantine,
            counters=self.counters,
            clock=clock,
        )
        self._sessions = AgentSessionAttributes()
        self.ownership = NameOwnership(clock=clock, offline_ttl=lease_offline_ttl)
        self.connected_clients: set[Any] = set()
        self.unauth_clients: set[Any] = set()
        self.agent_sockets: dict[str, Any] = {}
        self.socket_agent: dict[Any, str] = {}
        self.socket_quota_principals: dict[Any, str] = {}
        self._socket_identity_proofs: dict[Any, tuple[str, str]] = {}

    @property
    def max_clients(self) -> int:
        """Total live sockets the hub will hold."""
        return self._capacity.max_clients

    @property
    def max_unauth_clients(self) -> int:
        """Sockets allowed to sit in the pre-authentication window."""
        return self._capacity.max_unauth_clients

    @property
    def max_connections_per_host(self) -> int | None:
        """Sockets allowed from one remote host, or ``None`` when uncapped."""
        return self._capacity.max_connections_per_host

    @property
    def takeover_cooldown(self) -> float:
        """Minimum gap between accepted takeovers of one name."""
        return self._takeover.cooldown

    @property
    def takeover_oscillation_window(self) -> float:
        """How far back oscillation stamps still count."""
        return self._takeover.window

    @property
    def takeover_oscillation_threshold(self) -> int:
        """Accepted takeovers inside the window that open a quarantine."""
        return self._takeover.threshold

    @property
    def takeover_quarantine(self) -> float:
        """How long a quarantine pins the current owner."""
        return self._takeover.quarantine

    @property
    def agent_roles(self) -> dict[str, tuple[str, ...]]:
        """The role map, the same object the hub holds."""
        return self._sessions.roles

    def at_capacity(self) -> bool:
        """Return whether the total connection table is full."""
        return len(self.connected_clients) >= self.max_clients

    def unauthenticated_at_capacity(self) -> bool:
        """Return whether the pre-authentication connection table is full."""
        return len(self.unauth_clients) >= self.max_unauth_clients

    def host_at_capacity(self, websocket: Any) -> bool:
        """Return whether ``websocket``'s remote host already holds its socket cap."""
        return self._capacity.host_at_capacity(websocket)

    def fanout_clients(self) -> tuple[Any, ...]:
        """Return the sockets a one-to-many write may reach.

        On an open hub that is every connected socket. On a secured hub it is only
        the sockets bound to a name: a socket binds a name only after its first frame
        passed the token gate, so a socket still in its pre-authentication window
        receives no fan-out, and neither does one whose name was revoked or taken
        over. This is an allow-list on purpose: a socket that is connected but not
        bound is left out, whatever state it is in. Writes to one socket, such as a
        refusal, the welcome or a replay, do not come through here.

        Returns
        -------
        tuple
            The sockets, in the connection table's order.
        """
        if not self._secured:
            return tuple(self.connected_clients)
        return tuple(socket for socket in self.connected_clients if socket in self.socket_agent)

    def add_client(self, websocket: Any) -> None:
        """Record a newly admitted socket."""
        self.connected_clients.add(websocket)
        self._capacity.note_connected(websocket)

    def drop_client(self, websocket: Any) -> str | None:
        """Drop a socket and return the active agent name that disappeared, if any."""
        self.connected_clients.discard(websocket)
        self.socket_quota_principals.pop(websocket, None)
        self._socket_identity_proofs.pop(websocket, None)
        self._capacity.note_disconnected(websocket)
        name = self.socket_agent.pop(websocket, None)
        if name is not None and self.agent_sockets.get(name) == websocket:
            self.agent_sockets.pop(name, None)
            self._sessions.clear(name)
            self.ownership.mark_offline(name)
            return name
        return None

    def revoke_name(self, name: str) -> Any | None:
        """Detach ``name`` and release its ownership lease for operator recovery.

        This is the synchronous half of a governed reclaim, whether explicit
        break-glass or a consume-stale holder whose recovery TTL elapsed. The caller
        has already passed policy and removed the durable identity pin; detaching
        both registry maps before its next ``await`` prevents a new claimant from
        racing with a socket that still appears to own the name.
        The returned socket remains in ``connected_clients`` until its normal
        connection teardown runs, so the caller may send a final notice and
        close it without bypassing connection accounting.

        Parameters
        ----------
        name : str
            Agent identity whose live binding and lease are revoked.

        Returns
        -------
        object or None
            The previously bound socket, or ``None`` when the name was offline.
        """
        websocket = self.agent_sockets.pop(name, None)
        if websocket is not None:
            self.socket_agent.pop(websocket, None)
        self._sessions.clear(name)
        self.ownership.release(name)
        return websocket

    def add_unauthenticated(self, websocket: Any) -> None:
        """Record a socket in its secured-hub pre-authentication window."""
        self.unauth_clients.add(websocket)

    def discard_unauthenticated(self, websocket: Any) -> None:
        """Remove a socket from the secured-hub pre-authentication window."""
        self.unauth_clients.discard(websocket)

    def is_bound(self, websocket: Any) -> bool:
        """Return whether the socket has already bound an agent name."""
        return websocket in self.socket_agent

    def set_quota_principal(self, websocket: Any, principal: str) -> None:
        """Bind a non-empty claim-quota principal to one admitted socket.

        The binding is connection metadata, never a client-asserted frame field.
        Secured connections receive a credential fingerprint; open connections
        receive the ingress-derived remote-host fallback. Re-registration on the
        same socket cannot replace it because authentication runs only once.
        """
        value = principal.strip()
        if value:
            self.socket_quota_principals.setdefault(websocket, value)

    def quota_principal(self, websocket: Any, *, fallback_agent: str) -> str:
        """Return the socket's quota bucket or a compatibility fallback.

        Production ingress always binds a principal before sender resolution. The
        agent fallback preserves direct/unit integrations that invoke handlers
        without traversing ingress; it does not weaken the network path.
        """
        return self.socket_quota_principals.get(websocket, f"legacy-agent:{fallback_agent}")

    @staticmethod
    def is_reserved_sender(sender: str) -> bool:
        """Return whether ``sender`` impersonates a protocol or peer-hub identity.

        Matching is case-insensitive because downstream presentation layers may
        normalise case. Only the complete global name is reserved; a project-scoped
        identity such as ``PROJ/system`` remains an ordinary agent address. Every
        name containing ``@`` is reserved as well: that form denotes a seat on a
        peer hub (:mod:`synapse_channel.core.hub_address`), so a local client may
        never present a forwarded sender or a remote target as its own name. A name
        starting with ``role:`` is the mailbox key of a role
        (:mod:`synapse_channel.core.role_mailbox`), so no connection may take it.
        """
        return (
            sender.casefold() in _RESERVED_AGENT_NAMES
            or is_hub_qualified_name(sender)
            or is_role_mailbox_key(sender)
        )

    def record_identity_proof(self, websocket: Any, sender: str, key_id: str) -> None:
        """Remember that ``websocket`` registered as ``sender`` with a signature under ``key_id``.

        The caller records only a registration it verified against the operator's identity
        trust bundle; a trust-on-first-use proof, whose key the client chose, is never
        recorded.
        """
        self._socket_identity_proofs[websocket] = (sender, key_id)

    def identity_proof(self, websocket: Any) -> tuple[str, str] | None:
        """Return ``(sender, key_id)`` while ``websocket`` is still bound to that sender."""
        proof = self._socket_identity_proofs.get(websocket)
        if proof is None or self.socket_agent.get(websocket) != proof[0]:
            return None
        return proof

    def bound_agent(self, websocket: Any) -> str | None:
        """Return the agent name bound to the socket, if any."""
        return self.socket_agent.get(websocket)

    def set_agent_socket(self, sender: str, websocket: Any) -> bool:
        """Bind ``sender`` to ``websocket`` and return whether it was newly online."""
        is_new_agent = sender not in self.agent_sockets
        self.agent_sockets[sender] = websocket
        return is_new_agent

    def _owns(self, name: str, websocket: Any) -> bool:
        """Return whether ``websocket`` is the live owner of ``name``."""
        return (
            self.agent_sockets.get(name) is websocket and self.socket_agent.get(websocket) == name
        )

    def bind_protocol_version(self, name: str, websocket: Any, version: int | None) -> int:
        """Bind a peer's advertised wire version to its currently owned socket."""
        if not self._owns(name, websocket):
            raise DeliveryRefusal("unauthorised_requester", "socket is not bound to the identity")
        return self._sessions.bind_protocol_version(name, version)

    def protocol_version_of(self, name: str) -> int:
        """Return the bound peer version, or legacy version one when absent."""
        return self._sessions.protocol_version_of(name)

    def bind_delivery_session(
        self,
        name: str,
        websocket: Any,
        *,
        token: object,
        capabilities: object,
        hub_id: str,
    ) -> DeliverySession:
        """Bind a verified version-three session to its current authenticated socket.

        The caller must have completed the hub's name and identity gates. The
        256-bit process token is never stored: only a hub/name-separated digest
        is public. Reconnect with the same in-memory token retains the
        incarnation; a fresh process receives a different one.
        """
        if not self._owns(name, websocket):
            raise DeliveryRefusal("unauthorised_requester", "socket is not bound to the identity")
        return self._sessions.bind_delivery_session(
            name, token=token, capabilities=capabilities, hub_id=hub_id
        )

    def delivery_session(self, name: str) -> DeliverySession | None:
        """Return a copy of the current socket's advertised delivery contract."""
        return self._sessions.delivery_session(name)

    def set_roles(self, name: str, roles: tuple[str, ...]) -> None:
        """Bind the roles ``name`` answers to, replacing any previous set.

        An empty tuple clears the binding, so a re-register that drops a role removes
        it. Roles are additional ``<project>/<role>`` addresses, not exclusive — a role
        may be held by more than one agent, and a message to it reaches every holder.
        """
        self._sessions.set_roles(name, roles)

    def roles_of(self, name: str) -> tuple[str, ...]:
        """Return the roles ``name`` currently answers to (empty tuple if none)."""
        return self._sessions.roles_of(name)

    def set_wake_capability(self, name: str, capability: str) -> None:
        """Bind ``name`` to a declared receiver wake capability."""
        self._sessions.set_wake_capability(name, capability)

    def wake_capability_of(self, name: str) -> str:
        """Return ``name``'s declared wake capability, or ``unknown`` if absent."""
        return self._sessions.wake_capability_of(name)

    def _classify_takeover(self, sender: str, now: float) -> str:
        """Decide a takeover and leave every takeover map on the policy."""
        return self._takeover.classify(sender, now)

    async def _admit_owner(
        self,
        sender: str,
        websocket: Any,
        *,
        lease_requested: bool,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
    ) -> None:
        """Complete a successful bind: freeze lease expiry and grant when asked.

        Runs at every bind site the moment the registry maps point at the new
        owner. The lease mint happens in the synchronous prefix — before the
        grant frame's send awaits — so from the instant a takeover swap or a
        fresh bind completes, a concurrently arriving claim on the same name
        already sees the lease and is gated on its token. The grant frame goes
        only to the owning socket, never a broadcast: the token is a bearer
        credential.

        Parameters
        ----------
        sender : str
            The name that just bound.
        websocket : Any
            The socket that now owns it, and the only recipient of the grant.
        lease_requested : bool
            Whether the claimant opted into ownership leasing (the
            registration frame's ``lease`` field). Without it no lease is
            minted, preserving classic semantics for pre-lease clients.
        send_json : callable
            Coroutine used to deliver the grant frame.
        system : callable
            Factory for the grant frame payload.
        """
        self.ownership.mark_online(sender)
        if not lease_requested or self.ownership.is_leased(sender):
            return
        token = self.ownership.grant(sender)
        await send_json(
            websocket,
            system(
                f"Ownership lease granted for '{sender}'. Persist the token and "
                "present it as owner_lease on every reconnect.",
                msg_type=MessageType.LEASE_GRANTED,
                target=sender,
                owner_lease=token,
                lease_name=sender,
            ),
        )

    async def resolve_sender(
        self,
        sender: str,
        websocket: Any,
        *,
        takeover: bool,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
        lease_requested: bool = False,
        owner_lease: str = "",
    ) -> str | None:
        """Bind a socket to a sender name, enforcing ownership and takeover rules.

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
            conflict or a denied name switch, and the lease-grant frame.
        system : callable
            Factory for those system message payloads.
        lease_requested : bool, optional
            Whether the claimant asks for an ownership lease on the name it
            binds (the registration frame's ``lease`` field). Opt-in: absent,
            the name keeps today's first-come semantics, so a pre-lease client
            is never locked out of its own re-arm.
        owner_lease : str, optional
            The lease token the claimant presents for ``sender``. Required —
            and verified — whenever the name holds a live lease, whether its
            owner is currently connected or not.

        Returns
        -------
        str or None
            The resolved name, or ``None`` when the claim was refused and the
            socket closed.

        Notes
        -----
        The ownership gate runs before every other admission rule: a claim on
        a leased name that does not present the matching token is refused with
        close code ``4016`` (``"name owned"``) regardless of its ``takeover``
        flag, so a stranger can neither squat a leased name in its owner's
        reconnect gap nor evict the live owner. A claim that does present the
        token still passes through the takeover damping — cooldown, and the
        oscillation quarantine — so two processes sharing one lease cannot
        evict each other indefinitely.

        An accepted takeover rebinds ``agent_sockets`` and ``socket_agent`` to
        the new owner *synchronously, before* the eviction close handshake
        awaits. From any other task's point of view the name therefore switches
        owner atomically: no interleaving can resolve the name to the evicted
        socket or observe the name unheld mid-takeover. The lease mint shares
        that atomic prefix (see :meth:`_admit_owner`).
        """
        if self.is_reserved_sender(sender):
            logger.warning(
                "reserved identity refused sender=%s requester_host=%s",
                sender,
                self.remote_host(websocket),
            )
            text = (
                f"Name '{sender}' uses the '@' form reserved for seats on peer hubs. "
                "Choose a local agent name without '@'."
                if is_hub_qualified_name(sender)
                else f"Name '{sender}' is reserved for hub protocol provenance. "
                "Choose a non-reserved agent name."
            )
            await send_json(
                websocket,
                system(text, msg_type=MessageType.NAME_CONFLICT, target=sender),
            )
            await self.close_socket(websocket, code=4009, reason="reserved identity")
            return None

        known_sender = self.socket_agent.get(websocket)
        if known_sender is None:
            if self.ownership.is_leased(sender) and not self.ownership.matches(sender, owner_lease):
                logger.info(
                    "ownership refused sender=%s requester_host=%s reason=name owned",
                    sender,
                    self.remote_host(websocket),
                )
                await send_json(
                    websocket,
                    system(
                        f"Name '{sender}' is protected by an ownership lease. "
                        "Reconnect presenting its owner_lease token, wait "
                        f"{self.ownership.offline_ttl:.0f}s after its holder "
                        "disconnects for the lease to lapse, or choose a "
                        "unique --name.",
                        msg_type=MessageType.NAME_CONFLICT,
                        target=sender,
                    ),
                )
                await self.close_socket(websocket, code=4016, reason="name owned")
                return None
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
                    # The lease mint inside _admit_owner shares the swap's atomic
                    # prefix: it runs before this coroutine next suspends, so a
                    # concurrent claim already sees the lease it must match.
                    await self._admit_owner(
                        sender,
                        websocket,
                        lease_requested=lease_requested,
                        send_json=send_json,
                        system=system,
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
            await self._admit_owner(
                sender,
                websocket,
                lease_requested=lease_requested,
                send_json=send_json,
                system=system,
            )
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
        except Exception:  # nosec B110 # best-effort close; the socket may be gone
            pass

    @staticmethod
    def remote_host(websocket: Any) -> str:
        """Return the remote host of ``websocket`` for per-host rate keying."""
        address = getattr(websocket, "remote_address", None)
        if isinstance(address, (tuple, list)) and address:
            return str(address[0])
        return str(address) if address else "unknown"
