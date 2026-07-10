# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticate, name-resolve, and exposure-guard inbound sockets
"""Pre-route ingress guards for the routing hub.

:class:`HubIngress` owns the checks a frame passes before it reaches the routing
pipeline: authenticating a socket's first frame against the shared-secret token,
binding the claimed sender name (enforcing uniqueness, with optional takeover),
keying the remote host for per-host rate limiting, closing a socket, and refusing —
or, when overridden, warning about — an exposed bind. It reads the live
:class:`~synapse_channel.core.hub_clients.HubClientRegistry` rather than capturing a
snapshot, and takes the hub's per-socket send and system-message factory as injected
callbacks, so it carries no back-reference to the hub — the same callback-injection
:class:`~synapse_channel.core.hub_broadcast.HubBroadcaster` uses.

The exposure configuration (authenticator, whether metrics are served and how they
are gated, and the off-loopback override) is captured at construction because the
hub never mutates it after ``__init__``; the guard warns through a logger named
``synapse.hub`` so its records stay under the hub's log namespace.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_exposure import exposure_problems, guard_exposure
from synapse_channel.core.protocol import MessageType

logger = logging.getLogger("synapse.hub")


class HubIngress:
    """Guard an inbound socket before its frame is routed.

    Parameters
    ----------
    clients : HubClientRegistry
        The live socket registry; ``socket_agent`` (for the already-bound check)
        and ``resolve_sender`` are read fresh on each call so membership changes
        are always reflected.
    authenticator : TokenAuthenticator or None
        When set, a socket's first frame must present a valid shared-secret token
        or it is refused and closed; ``None`` leaves the hub open (a loopback bind).
    enable_metrics : bool
        Whether the hub also serves HTTP ``/metrics`` and ``/health``; consulted by
        the exposure guard because a served-but-untokened metrics endpoint is a
        problem off loopback.
    metrics_token : str or None
        The token gating the metrics endpoint, if any; ``None`` leaves it open.
    metrics_query_token_ok : bool
        Whether the metrics token may be presented as a ``?token=`` query parameter;
        accepting it is an off-loopback problem because it leaks into URL logs.
    insecure_off_loopback : bool
        When ``True`` the exposure guard warns and proceeds instead of raising
        :class:`~synapse_channel.core.hub_exposure.InsecureBindError`.
    send_json : Callable[[Any, dict], Awaitable[None]]
        The hub's per-socket send (``hub._send_json``), used to deliver the
        auth-denied and name-conflict replies.
    system : Callable[..., dict]
        The hub's system-message factory (``hub._system``), used to stamp those
        replies with the hub id.
    """

    def __init__(
        self,
        clients: HubClientRegistry,
        *,
        authenticator: TokenAuthenticator | None,
        enable_metrics: bool,
        metrics_token: str | None,
        metrics_query_token_ok: bool,
        insecure_off_loopback: bool,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
    ) -> None:
        self._clients = clients
        self._authenticator = authenticator
        self._enable_metrics = enable_metrics
        self._metrics_token = metrics_token
        self._metrics_query_token_ok = metrics_query_token_ok
        self._insecure_off_loopback = insecure_off_loopback
        self._send_json = send_json
        self._system = system

    async def authorise(self, sender: str, data: dict[str, Any], websocket: Any) -> bool:
        """Gate the first message from a socket on the shared-secret token.

        Authentication is checked once, when a socket first binds a name; later
        messages on an already-bound socket are trusted. With no authenticator
        the hub is open.

        Parameters
        ----------
        sender : str
            The agent name the connection claims.
        data : dict[str, Any]
            The decoded message; the token is read from its ``token`` field.
        websocket : Any
            The sender's socket, closed (code ``4010``) when authentication fails.

        Returns
        -------
        bool
            ``True`` when the message may proceed, ``False`` when it was refused
            and the socket closed.
        """
        if self._authenticator is None or self._clients.socket_agent.get(websocket) is not None:
            return True
        ok, reason = self._authenticator.authenticate(str(data.get("token") or ""), sender)
        if ok:
            return True
        await self._send_json(
            websocket,
            self._system(reason, msg_type=MessageType.AUTH_DENIED, target=sender),
        )
        await websocket.close(code=4010, reason="auth denied")
        return False

    async def resolve_sender(
        self,
        sender: str,
        websocket: Any,
        *,
        takeover: bool = False,
        lease_requested: bool = False,
        owner_lease: str = "",
    ) -> str | None:
        """Bind a socket to a sender name, enforcing ownership and uniqueness.

        When ``takeover`` is set and the name is held by another (possibly stale)
        socket, the holder is evicted and the name rebound to the newcomer — this
        lets a re-arming waiter reclaim its own ``<name>-rx`` from a ghost connection
        without waiting for the keepalive ping to reap it. A name protected by an
        ownership lease additionally requires the matching ``owner_lease`` token
        before any of that applies; ``lease_requested`` asks for a lease on a name
        that has none (see
        :meth:`~synapse_channel.core.hub_clients.HubClientRegistry.resolve_sender`).

        Returns the resolved name, or ``None`` when a name conflict or ownership
        refusal closed the socket.
        """
        return await self._clients.resolve_sender(
            sender,
            websocket,
            takeover=takeover,
            send_json=self._send_json,
            system=self._system,
            lease_requested=lease_requested,
            owner_lease=owner_lease,
        )

    def exposure_problems(self, host: str) -> list[str]:
        """Return the exposure problems for binding on ``host`` (empty when safe).

        A loopback bind is always safe. Off loopback, a hub with no token — or
        with metrics served but no metrics token, or with the metrics query-string
        token accepted (it would leak into URL logs) — is a human-readable problem.
        """
        return exposure_problems(
            host,
            authenticator=self._authenticator,
            enable_metrics=self._enable_metrics,
            metrics_token=self._metrics_token,
            metrics_query_token_ok=self._metrics_query_token_ok,
        )

    def guard_exposure(self, host: str) -> None:
        """Refuse — or, when overridden, warn — before binding an exposed host.

        Off loopback without the matching guard the hub would be reachable
        unauthenticated. By default this raises
        :class:`~synapse_channel.core.hub_exposure.InsecureBindError` so the bus is
        never accidentally exposed; with ``insecure_off_loopback`` set the problems
        are logged as warnings and the bind proceeds.
        """
        guard_exposure(
            host,
            authenticator=self._authenticator,
            enable_metrics=self._enable_metrics,
            metrics_token=self._metrics_token,
            metrics_query_token_ok=self._metrics_query_token_ok,
            insecure_off_loopback=self._insecure_off_loopback,
            logger=logger,
        )

    @staticmethod
    async def close_socket(websocket: Any, *, code: int, reason: str) -> None:
        """Close a websocket and wait for close propagation when supported."""
        await HubClientRegistry.close_socket(websocket, code=code, reason=reason)

    @staticmethod
    def remote_host(websocket: Any) -> str:
        """Return the remote host of ``websocket`` for per-host rate keying.

        Accepts the ``(host, port)`` tuple the websockets server exposes, a bare
        address, or nothing, collapsing to ``"unknown"`` so the per-host bucket
        always has a stable key.
        """
        return HubClientRegistry.remote_host(websocket)
