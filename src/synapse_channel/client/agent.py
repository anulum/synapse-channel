# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reusable async WebSocket client for joining the hub
"""Reusable asynchronous agent client for the Synapse hub.

:class:`SynapseAgent` wraps a single WebSocket connection to the hub: it sends
the registration heartbeat, keeps the connection alive with periodic
heartbeats, forwards every inbound message to a user callback, and exposes
typed helpers for the coordination verbs (chat, claim, release, and the
``state``/``who``/``history`` queries). It is the building block the worker,
the CLI, and any embedding application use to appear on the channel.
"""

from __future__ import annotations

import asyncio
import logging

from websockets.asyncio.client import ClientConnection

from synapse_channel.client.agent_dispatch import AgentDispatchMixin, MessageCallback
from synapse_channel.client.agent_lifecycle import (
    DEFAULT_HUB_URI,
    MINIMUM_HEARTBEAT_INTERVAL,
    AgentLifecycleMixin,
    _is_connection_refused,
)
from synapse_channel.client.agent_outbound import AgentOutboundMixin
from synapse_channel.client.agent_queries import AgentQueryMixin

logging.basicConfig(level=logging.ERROR)

__all__ = [
    "DEFAULT_HUB_URI",
    "MINIMUM_HEARTBEAT_INTERVAL",
    "MessageCallback",
    "SynapseAgent",
    "_is_connection_refused",
]


class SynapseAgent(AgentLifecycleMixin, AgentDispatchMixin, AgentOutboundMixin, AgentQueryMixin):
    """An async client that maintains one connection to the Synapse hub.

    Parameters
    ----------
    name : str
        Unique agent name presented to the hub.
    on_message_callback : MessageCallback or None, optional
        Coroutine called with every decoded inbound message. Self-originated
        chat echoes are filtered out before the callback runs.
    uri : str, optional
        Hub WebSocket URI. Defaults to :data:`DEFAULT_HUB_URI`.
    heartbeat_interval : float, optional
        Seconds between keepalive heartbeats, clamped up to
        :data:`MINIMUM_HEARTBEAT_INTERVAL`. Defaults to ``20.0``.
    verbose : bool, optional
        When ``True``, connection lifecycle notes are printed. Defaults to ``True``.
    token : str or None, optional
        Shared-secret token presented on the registration message when the hub
        requires authentication. ``None`` sends no token (the default for an
        open, loopback hub).
    ping_interval : float, optional
        Seconds between client keepalive pings, so a half-open connection — a hub
        that was killed, an ungraceful restart, or an eviction whose close frame
        never arrived — is detected and :meth:`connect` returns instead of blocking
        forever. Without this a waiter can linger for days holding a dead socket.
        Defaults to ``20.0``.
    ping_timeout : float, optional
        Seconds to wait for a ping reply before dropping the connection. Defaults
        to ``20.0``.
    """

    def __init__(
        self,
        name: str,
        on_message_callback: MessageCallback | None = None,
        *,
        uri: str = DEFAULT_HUB_URI,
        heartbeat_interval: float = 20.0,
        verbose: bool = True,
        token: str | None = None,
        takeover: bool = False,
        ping_interval: float = 20.0,
        ping_timeout: float = 20.0,
    ) -> None:
        self.name = name
        self.uri = uri
        self.connection: ClientConnection | None = None
        self.callback = on_message_callback
        self.running = True
        self.heartbeat_interval = max(float(heartbeat_interval), MINIMUM_HEARTBEAT_INTERVAL)
        self._heartbeat_task: asyncio.Task[None] | None = None
        self.ready_event = asyncio.Event()
        self.last_close_code: int | None = None
        self.last_close_reason: str = ""
        self.hub_id = "unknown"
        self.verbose = bool(verbose)
        self.token = token
        self.takeover = bool(takeover)
        self.ping_interval = float(ping_interval)
        self.ping_timeout = float(ping_timeout)
