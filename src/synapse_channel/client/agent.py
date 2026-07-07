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
    HUB_URI_ENV_VAR,
    MINIMUM_HEARTBEAT_INTERVAL,
    AgentLifecycleMixin,
    _is_connection_refused,
    default_hub_uri,
)
from synapse_channel.client.agent_outbound import AgentOutboundMixin
from synapse_channel.client.agent_queries import AgentQueryMixin
from synapse_channel.core.message_auth import MessageAuthKey

logging.basicConfig(level=logging.ERROR)

__all__ = [
    "DEFAULT_HUB_URI",
    "HUB_URI_ENV_VAR",
    "MINIMUM_HEARTBEAT_INTERVAL",
    "MessageCallback",
    "SynapseAgent",
    "_is_connection_refused",
    "default_hub_uri",
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
    takeover : bool, optional
        When ``True``, the registration asks the hub to evict a stale holder of
        ``name`` instead of failing with a name conflict. Defaults to ``False``.
    roles : tuple of str, optional
        Full ``<project>/<role>`` names this identity also answers to, declared on
        the registration heartbeat so the hub binds them — a directed message to a
        role then reaches this agent and the role shows in ``/who``. Empty by default.
    mailbox : bool, optional
        When ``True``, the registration heartbeat declares ``mailbox: true`` and the
        agent's ``since_seq`` cursor, so a mailbox-capable hub replays the directed
        messages missed while offline; the agent then advances its cursor on every
        chat frame it sees and acknowledges each replayed frame (which lets the hub
        confirm a deferred delivery receipt to the original sender). Defaults to
        ``False`` — an ordinary agent neither asks for a replay nor acks.
    mailbox_since_seq : int, optional
        The durable journal ``seq`` the agent has already processed, used to seed
        the cursor so a caller that persists it across reconnects resumes from where
        it left off rather than replaying the whole retained window. Floored at ``0``
        (the whole window). Defaults to ``0``.
    mailbox_for : str, optional
        The identity whose backlog to replay, when it differs from ``name``. A
        wake-listener connects under a receive-only ``name`` (an ``-rx`` suffix) but
        waits on its bare identity, so it sets this to that identity and the hub
        filters the replay by it rather than by the connection name. Empty (the
        default) leaves the hub replaying the backlog for ``name`` itself — correct
        for an agent that connects under its own identity.
    per_message_auth_key_id : str or None, optional
        Key id used to sign mutating frames with per-message authentication.
        ``None`` leaves frame signing off.
    per_message_auth_secret : str or bytes or None, optional
        HMAC secret paired with ``per_message_auth_key_id``. Both fields must be
        set to sign frames.
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
        roles: tuple[str, ...] = (),
        mailbox: bool = False,
        mailbox_since_seq: int = 0,
        mailbox_for: str = "",
        per_message_auth_key_id: str | None = None,
        per_message_auth_secret: str | bytes | None = None,
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
        self.hub_protocol_version: int | None = None
        self.verbose = bool(verbose)
        self.token = token
        self.takeover = bool(takeover)
        self.roles = tuple(roles)
        self.mailbox = bool(mailbox)
        self._mailbox_since_seq = max(0, int(mailbox_since_seq))
        self.mailbox_for = str(mailbox_for)
        self._message_auth_key: MessageAuthKey | None = None
        if per_message_auth_key_id is not None and per_message_auth_secret is not None:
            secret = (
                per_message_auth_secret
                if isinstance(per_message_auth_secret, bytes)
                else per_message_auth_secret.encode("utf-8")
            )
            self._message_auth_key = MessageAuthKey(
                key_id=str(per_message_auth_key_id), secret=secret, senders=frozenset({name})
            )
        self._message_auth_sequence = 0
        self.ping_interval = float(ping_interval)
        self.ping_timeout = float(ping_timeout)

    @property
    def mailbox_cursor(self) -> int:
        """Return the highest durable journal ``seq`` this agent has processed.

        Starts at the seeded ``mailbox_since_seq`` and advances as the agent sees
        chat frames, so a caller that persists it across reconnects — a waiter
        re-armed as a fresh process — can seed the next agent's ``mailbox_since_seq``
        and resume the backlog from where this one stopped rather than from zero.
        """
        return self._mailbox_since_seq
