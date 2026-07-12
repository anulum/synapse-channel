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
from collections.abc import Callable
from typing import Any

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
from synapse_channel.core.capability_card_signing import (
    DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS,
)
from synapse_channel.core.identity_keys import load_signing_key
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.wake_capability import WAKE_DIRECT, normalize_wake_capability
from synapse_channel.machine_identity import machine_identity_agent_kwargs

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
        chat frame admitted by its acceptance gate and acknowledges live and replayed
        frames. The hub uses that receiver watermark for pending counts and can also
        confirm a deferred delivery receipt to the original sender. This does not
        acknowledge model processing. Defaults to ``False`` — an ordinary agent
        neither asks for a replay nor acks.
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
    mailbox_advance : Callable or None, optional
        Gate consulted before the mailbox cursor advances past a chat frame (and
        before a live or replayed frame is acknowledged). A waiter that surfaces only a
        FILTERED subset of frames passes its wake filter here, so a frame it will
        never show cannot be silently consumed: an unadvanced cursor leaves the
        frame pending and a later (or correctly bound) waiter still receives it
        on replay. ``None`` (the default) advances on every chat frame — correct
        for an ordinary client whose callback processes everything it receives.
    wake_capability : str, optional
        Receiver capability declared on the registration heartbeat. Ordinary agents
        default to ``direct``; passive wait sockets and pane bridges override it.
    request_lease : bool, optional
        When ``True``, the registration heartbeat declares ``lease: true``, asking
        the hub for an ownership lease on the bound name: the hub then admits a
        later claim on that name only when it presents the granted token, so a
        reconnect re-takes its own name and a stranger cannot squat it in the gap.
        Off by default — a client that does not opt in keeps classic first-come
        name semantics, and a pre-lease hub ignores the field entirely.
    owner_lease : str, optional
        The lease token to present for the bound name, persisted from an earlier
        grant (see :mod:`synapse_channel.owner_lease`). Empty (the default)
        presents nothing, which is correct for a first claim. Updated in place
        when the hub grants a fresh lease.
    on_lease_granted : Callable[[str], None] or None, optional
        Called with the token the moment the hub grants a lease, so the caller
        can persist it before the process exits. ``None`` (the default) only
        records the token on :attr:`owner_lease`.
    machine_identity : bool, optional
        Present the zero-config trust-on-first-use machine key when no explicit
        ``identity_key_path`` is given (the default). Resolution is best-effort:
        a core-only install or an unreadable key degrades to an unsigned
        connection with the module's stated one-time warning. Pass ``False``
        for a deliberately unsigned agent — a hub enforcing an identity pin for
        the name will then refuse the connection, by design. An explicit
        ``identity_key_path`` always wins over the machine default.
    per_message_auth_key_id : str or None, optional
        Key id used to sign mutating frames with per-message authentication.
        ``None`` leaves frame signing off.
    per_message_auth_secret : str or bytes or None, optional
        HMAC secret paired with ``per_message_auth_key_id``. Both fields must be
        set to sign frames.
    capability_card_key_path : str or None, optional
        Owner-only Ed25519 PEM used only to sign capability advertisements.
    capability_card_key_id : str, optional
        Public id of ``capability_card_key_path`` in the hub's separate card trust
        bundle. A path and id must be supplied together.
    capability_card_project : str, optional
        Optional assertion of the namespace prefix in ``name``. Signed live cards
        require a namespaced agent, and this value must match that hub-resolved prefix.
    capability_card_lifetime_seconds : float, optional
        Lifetime recorded in each signed advertisement.
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
        mailbox_advance: Callable[[dict[str, Any]], bool] | None = None,
        wake_capability: str = WAKE_DIRECT,
        request_lease: bool = False,
        owner_lease: str = "",
        on_lease_granted: Callable[[str], None] | None = None,
        per_message_auth_key_id: str | None = None,
        per_message_auth_secret: str | bytes | None = None,
        identity_key_path: str | None = None,
        identity_key_id: str = "",
        capability_card_key_path: str | None = None,
        capability_card_key_id: str = "",
        capability_card_project: str = "",
        capability_card_lifetime_seconds: float = DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS,
        machine_identity: bool = True,
        ping_interval: float = 20.0,
        ping_timeout: float = 20.0,
    ) -> None:
        if identity_key_path is None and machine_identity:
            # Present the zero-config machine identity by default. Every verb
            # that builds an agent signs uniformly — the 2026-07-10 incident
            # 1603 lockout class, where arming pinned a name and every other
            # (unsigned) verb was then refused 4013, cannot recur through a
            # forgotten call site. Best-effort by contract: on a core-only
            # install or an unreadable key this resolves to nothing and the
            # connection proceeds unsigned, exactly as before.
            resolved = machine_identity_agent_kwargs()
            if resolved:
                identity_key_path = str(resolved["identity_key_path"])
                identity_key_id = identity_key_id or str(resolved["identity_key_id"])
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
        self.mailbox_advance = mailbox_advance
        self.wake_capability = normalize_wake_capability(wake_capability, default=WAKE_DIRECT)
        self.request_lease = bool(request_lease)
        self.owner_lease = str(owner_lease)
        self.on_lease_granted = on_lease_granted
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
        self._identity_key = load_signing_key(identity_key_path) if identity_key_path else None
        self._identity_key_id = str(identity_key_id)
        self._identity_sequence = 0
        if bool(capability_card_key_path) != bool(capability_card_key_id.strip()):
            raise ValueError(
                "capability_card_key_path and capability_card_key_id must be supplied together"
            )
        inferred_project = name.split("/", 1)[0] if "/" in name else ""
        requested_project = str(capability_card_project).strip()
        if capability_card_key_path and not inferred_project:
            raise ValueError("a signed live capability card requires a namespaced agent name")
        if capability_card_key_path and requested_project not in ("", inferred_project):
            raise ValueError(
                "capability_card_project must match the agent namespace resolved by the hub"
            )
        self._capability_card_project = inferred_project or requested_project
        self._capability_card_key = (
            load_signing_key(capability_card_key_path) if capability_card_key_path else None
        )
        self._capability_card_key_id = str(capability_card_key_id).strip()
        self._capability_card_sequence = 0
        self._capability_card_lifetime_seconds = float(capability_card_lifetime_seconds)
        if (
            self._capability_card_lifetime_seconds <= 0.0
            or self._capability_card_lifetime_seconds != self._capability_card_lifetime_seconds
            or self._capability_card_lifetime_seconds == float("inf")
        ):
            raise ValueError("capability_card_lifetime_seconds must be finite and positive")
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
