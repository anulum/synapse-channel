# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Central WebSocket hub for the Synapse coordination bus.

:class:`SynapseHub` is the single source of truth for the channel: it tracks
connected sockets and named agents, enforces unique agent names, relays chat and
targeted messages, persists chat history, and delegates claim/task/resource
bookkeeping to a :class:`~synapse_channel.core.state.SynapseState`. All routing state
lives on the instance — there are no module globals — so several hubs can run in
one process, which keeps the routing logic deterministic and unit-testable.

Each message type is handled by a free coroutine registered in
:data:`~synapse_channel.core.handlers.DISPATCH`; the hub parses and authorises a
frame, resolves its sender, then looks the type up and awaits its handler, so the
routing core stays a table lookup rather than a growing branch ladder.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import signal
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import websockets
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability import CapabilityRegistry
from synapse_channel.core.handlers import DISPATCH
from synapse_channel.core.idempotency import IdempotencyCache
from synapse_channel.core.journal import record_idempotency, replay
from synapse_channel.core.ledger import DEFAULT_MAX_PROGRESS, Blackboard
from synapse_channel.core.metrics import (
    HEALTH_CONTENT_TYPE,
    PROMETHEUS_CONTENT_TYPE,
    collect_hub_metrics,
    health_snapshot,
    render_prometheus,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import (
    RESOURCE_TYPE_ALIASES,
    MessageType,
    loads_bounded,
    system_message,
)
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import (
    MAX_CLAIMS_PER_AGENT,
    MAX_OFFERS_PER_AGENT,
    SynapseState,
)
from synapse_channel.relay import append_jsonl, encode_lite, trim_jsonl_tail

logger = logging.getLogger("synapse.hub")

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8876
DEFAULT_MAX_HISTORY = 10000
DEFAULT_MAX_QUEUE = 64
DEFAULT_RELAY_MAX_LINES = 5000
DEFAULT_PING_INTERVAL = 15.0
"""Seconds between server keepalive pings, so a dead socket is detected promptly."""
DEFAULT_PING_TIMEOUT = 15.0
"""Seconds to wait for a ping reply before dropping the connection and freeing its name."""
DEFAULT_MAX_CLIENTS = 64
"""Maximum simultaneous connections; a further connect is closed with code 4013."""
DEFAULT_MAX_MSG_BYTES = 1024 * 1024
"""Largest accepted inbound frame (bytes); a larger one is rejected by the transport."""
DEFAULT_TAKEOVER_COOLDOWN = 2.0
"""Seconds a name is protected from a second takeover, to blunt an eviction storm."""
DEFAULT_AUTH_TIMEOUT = 10.0
"""Seconds a secured hub waits for an authenticated first frame before closing a socket."""
MAX_LOG_PAYLOAD = 120
"""Characters of a message payload logged at INFO before it is truncated."""
DEFAULT_COMPACT_HINT_THRESHOLD = 100_000
"""Event-log record count past which the hub logs a one-off ``synapse compact`` hint.

The durable log grows append-only and is never auto-compacted — pruning is safe only
below a sequence the read-side has already consumed, which the hub cannot know. So
instead of silently growing or unsafely trimming, a hub started on a log larger than
this emits a single startup hint to run :class:`compact` manually."""

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
"""Bind hosts treated as loopback-only, where running without a token is fine."""


def is_loopback_host(host: str) -> bool:
    """Return whether ``host`` binds only the loopback interface."""
    return host.strip().lower() in LOOPBACK_HOSTS


_MUTATING_TYPES = (
    frozenset(
        {
            MessageType.CLAIM,
            MessageType.RELEASE,
            MessageType.TASK_UPDATE,
            MessageType.HANDOFF,
            MessageType.CHECKPOINT,
        }
    )
    | RESOURCE_TYPE_ALIASES
)
"""Inbound message types eligible for idempotent replay protection."""


class SynapseHub:
    """Routing core that maintains presence, history, and coordination state.

    Parameters
    ----------
    default_ttl_seconds : float, optional
        Lease TTL passed to the underlying :class:`SynapseState`. Defaults to
        ``3600.0``.
    hub_id : str or None, optional
        Stable hub identifier stamped on outgoing system messages. When ``None``
        a random ``"syn-XXXXXXXX"`` id is generated.
    journal : EventStore or None, optional
        When given, authoritative mutations are appended to this durable log and
        the hub's state is rebuilt from it on construction, so a restart resumes
        live leases and history instead of an empty registry. When ``None`` the
        hub is purely in-memory.
    rate_limiter : RateLimiter or None, optional
        When given, non-heartbeat messages from an agent over its limit are
        refused, so one runaway agent cannot swamp the single hub. ``None``
        disables rate limiting.
    host_rate_limiter : RateLimiter or None, optional
        When given, every inbound frame — heartbeats included — is charged to a
        bucket keyed by the connection's remote host, so a single host cannot flood
        the hub by cycling agent names or with bare heartbeats. Independent of and
        additional to ``rate_limiter``; ``None`` disables the per-host ceiling.
    max_history : int, optional
        Maximum chat messages retained in memory; the oldest are dropped beyond
        this bound so history cannot grow without limit. The durable log (when a
        journal is attached) still records every message. Defaults to
        :data:`DEFAULT_MAX_HISTORY`.
    relay_log : str or pathlib.Path or None, optional
        When given, every broadcast message is also mirrored to this newline-
        delimited log in the compact lite format (see
        :func:`~synapse_channel.relay.encode_lite`), so a token-budgeted agent
        can observe the channel by tailing a file instead of holding a socket.
        ``None`` disables the mirror.
    relay_max_lines : int, optional
        Upper bound on the relay log: it is trimmed back to its last this-many
        lines once it grows that far past the bound, so the mirror cannot grow
        without limit. Defaults to :data:`DEFAULT_RELAY_MAX_LINES`.
    max_progress : int, optional
        Maximum progress notes retained on the shared blackboard; the oldest are
        dropped beyond this bound. The durable log (when attached) still records
        every note. Defaults to :data:`~synapse_channel.core.ledger.DEFAULT_MAX_PROGRESS`.
    compact_hint_threshold : int, optional
        Record count past which a hub started on a durable log emits a one-off
        startup hint to run ``synapse compact`` (the log is never auto-compacted —
        pruning is safe only below a consumed read-side cursor). Clamped up to
        ``1``; set it very high to silence the hint. Defaults to
        :data:`DEFAULT_COMPACT_HINT_THRESHOLD`.
    authenticator : TokenAuthenticator or None, optional
        When given, a connecting agent must present a valid shared-secret token
        on its first message or the hub refuses and closes the socket. ``None``
        leaves the hub open, which is the right default for a loopback bind.
    enable_metrics : bool, optional
        When ``True`` the server also answers HTTP ``GET /metrics`` (Prometheus
        text exposition) and ``GET /health`` (a JSON liveness document) on the
        same port as the WebSocket endpoint, for scraping and container probes.
        Off by default — a plain WebSocket hub serves no HTTP.
    auth_timeout : float, optional
        On a secured hub (``authenticator`` set), seconds to wait for an
        authenticated first frame before closing the socket. Until a socket
        authenticates it is never shown the roster (no ``WELCOME``) and an idle
        unauthenticated socket is reaped at this deadline so it cannot hold a
        connection slot. Ignored on an open hub. Defaults to
        :data:`DEFAULT_AUTH_TIMEOUT`.
    max_unauth_clients : int or None, optional
        On a secured hub, the most sockets allowed in their pre-auth window at once;
        a further connect is closed with code ``4014`` so an authentication-stall
        burst cannot fill the connection table for the whole ``auth_timeout``.
        ``None`` (the default) tracks ``max_clients``, i.e. no extra restriction
        until an operator sets a tighter value. Ignored on an open hub.
    metrics_token : str or None, optional
        When set (and ``enable_metrics`` is on), ``GET /metrics`` and ``GET
        /health`` require this token — presented as ``Authorization: Bearer
        <token>`` — and answer ``401`` without it, so an exposed metrics endpoint
        does not leak operational metadata. ``None`` leaves the endpoint open, which
        is the right default for a loopback bind.
    metrics_query_token_ok : bool, optional
        Also accept the token as a ``?token=<token>`` query parameter. Off by
        default because a query token can leak into access logs, shell history, and
        proxy records; the ``Authorization`` header is the recommended path.
    """

    def __init__(
        self,
        *,
        default_ttl_seconds: float = 3600.0,
        hub_id: str | None = None,
        journal: EventStore | None = None,
        rate_limiter: RateLimiter | None = None,
        host_rate_limiter: RateLimiter | None = None,
        max_history: int = DEFAULT_MAX_HISTORY,
        relay_log: str | Path | None = None,
        relay_max_lines: int = DEFAULT_RELAY_MAX_LINES,
        max_progress: int = DEFAULT_MAX_PROGRESS,
        compact_hint_threshold: int = DEFAULT_COMPACT_HINT_THRESHOLD,
        authenticator: TokenAuthenticator | None = None,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        max_unauth_clients: int | None = None,
        max_msg_bytes: int = DEFAULT_MAX_MSG_BYTES,
        max_claims_per_agent: int = MAX_CLAIMS_PER_AGENT,
        max_offers_per_agent: int = MAX_OFFERS_PER_AGENT,
        max_paths_per_claim: int = MAX_DECLARED_PATHS,
        takeover_cooldown: float = DEFAULT_TAKEOVER_COOLDOWN,
        enable_metrics: bool = False,
        auth_timeout: float = DEFAULT_AUTH_TIMEOUT,
        metrics_token: str | None = None,
        metrics_query_token_ok: bool = False,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.journal = journal
        self.enable_metrics = bool(enable_metrics)
        self.auth_timeout = max(float(auth_timeout), 0.1)
        self.metrics_token = metrics_token or None
        self.metrics_query_token_ok = bool(metrics_query_token_ok)
        self.rate_limiter = rate_limiter
        self.host_rate_limiter = host_rate_limiter
        self.authenticator = authenticator
        self.max_clients = max(int(max_clients), 1)
        self.max_unauth_clients = (
            self.max_clients if max_unauth_clients is None else max(int(max_unauth_clients), 1)
        )
        self.max_msg_bytes = max(int(max_msg_bytes), 1)
        self.takeover_cooldown = max(float(takeover_cooldown), 0.0)
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self._last_takeover: dict[str, float] = {}
        self.max_history = max(int(max_history), 1)
        self.compact_hint_threshold = max(1, int(compact_hint_threshold))
        self.relay_log = Path(relay_log) if relay_log else None
        self.relay_max_lines = max(int(relay_max_lines), 1)
        self._relay_appends = 0
        self.hub_id = hub_id or f"syn-{uuid.uuid4().hex[:8]}"
        self.connected_clients: set[Any] = set()
        self.unauth_clients: set[Any] = set()
        self.agent_sockets: dict[str, Any] = {}
        self.socket_agent: dict[Any, str] = {}
        self._idempotency = IdempotencyCache()
        self._waits: dict[str, str] = {}
        self.capabilities = CapabilityRegistry()
        if journal is not None:
            replayed = replay(
                journal,
                default_ttl_seconds=default_ttl_seconds,
                max_progress=max_progress,
                max_claims_per_agent=max_claims_per_agent,
                max_offers_per_agent=max_offers_per_agent,
                max_paths_per_claim=max_paths_per_claim,
            )
            self.state = replayed.state
            self.chat_history = replayed.chat_history[-self.max_history :]
            self._message_seq = replayed.message_seq
            self.blackboard = replayed.blackboard
            # Rebuild the at-most-once guard so a retry after a restart replays the
            # original response instead of re-applying the mutation. Seeded oldest
            # first, the bounded cache keeps the most-recent keys.
            for idem_key, idem_response in replayed.idempotency:
                self._idempotency.put(idem_key, idem_response)
            # The durable log is append-only and never auto-compacted (pruning is safe
            # only below a sequence the read-side has consumed, which the hub cannot
            # know); a hub started on an oversized log emits one hint to compact manually.
            record_count = journal.count()
            if record_count > self.compact_hint_threshold:
                logger.warning(
                    "Event log holds %d records (over the %d hint threshold); it grows "
                    "append-only and is never auto-compacted. Run `synapse compact <db>` "
                    "to bound it — safe only below a sequence the read-side has consumed.",
                    record_count,
                    self.compact_hint_threshold,
                )
        else:
            self.state = SynapseState(
                default_ttl_seconds=default_ttl_seconds,
                max_claims_per_agent=max_claims_per_agent,
                max_offers_per_agent=max_offers_per_agent,
                max_paths_per_claim=max_paths_per_claim,
            )
            self.chat_history = []
            self._message_seq = 0
            self.blackboard = Blackboard(max_progress=max_progress)

    # -- helpers --------------------------------------------------------------

    def _next_msg_id(self) -> int:
        """Return a strictly increasing per-hub message sequence number."""
        self._message_seq += 1
        return self._message_seq

    @staticmethod
    def _idempotency_key(data: dict[str, Any]) -> str:
        """Return the client-supplied idempotency key, or an empty string."""
        return str(data.get("idem_key") or "")

    def _remember(self, data: dict[str, Any], response: dict[str, Any]) -> None:
        """Cache the response of an applied mutation under its idempotency key.

        The cache is also journalled when a durable log is attached, so the
        at-most-once guarantee survives a hub restart (a retried mutation replays
        the original response rather than re-applying).
        """
        key = self._idempotency_key(data)
        if key:
            self._idempotency.put(key, response)
            if self.journal is not None:
                record_idempotency(self.journal, key, response)

    async def _maybe_replay_duplicate(
        self, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Replay the cached response for a duplicate mutation, if any.

        Parameters
        ----------
        msg_type : str
            The inbound message type.
        data : dict[str, Any]
            The decoded message.
        websocket : Any
            The sender's socket.

        Returns
        -------
        bool
            ``True`` when the message was a recognised duplicate of an already
            applied mutation and its original response was re-sent to the sender;
            ``False`` when the message should be processed normally.
        """
        if msg_type not in _MUTATING_TYPES:
            return False
        key = self._idempotency_key(data)
        if not key:
            return False
        cached = self._idempotency.get(key)
        if cached is None:
            return False
        await self._send_json(websocket, cached)
        return True

    def _system(self, payload: str, **extra: Any) -> dict[str, Any]:
        """Build a hub system message stamped with this hub's id."""
        return system_message(payload, hub_id=self.hub_id, **extra)

    @staticmethod
    def _redact_payload(payload: str) -> str:
        """Truncate a message payload for the INFO log so it cannot bloat the log.

        A long payload (e.g. a large tool argument or pasted blob) is cut to
        :data:`MAX_LOG_PAYLOAD` characters with a count of how many were elided, so
        a single message cannot write an unbounded amount to the log.
        """
        if len(payload) <= MAX_LOG_PAYLOAD:
            return payload
        return f"{payload[:MAX_LOG_PAYLOAD]}…(+{len(payload) - MAX_LOG_PAYLOAD} chars)"

    def online_agents(self) -> list[str]:
        """Return the sorted names of currently registered agents."""
        return sorted(self.agent_sockets.keys())

    def uptime_seconds(self) -> float:
        """Return seconds elapsed since the hub was constructed."""
        return max(0.0, self._clock() - self._started)

    async def _send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        """Serialise and send one message to a single socket."""
        await websocket.send(json.dumps(data))

    def _mirror_to_relay(self, data: dict[str, Any]) -> None:
        """Append one broadcast message to the lite relay log, if configured.

        The log is written even when no socket is connected — its whole point is
        to let an observer catch up from the file later. It is trimmed back to
        :attr:`relay_max_lines` once that many lines have been appended since the
        last trim, bounding the file to roughly twice that many lines.
        """
        if self.relay_log is None:
            return
        append_jsonl(self.relay_log, encode_lite(data))
        self._relay_appends += 1
        if self._relay_appends >= self.relay_max_lines:
            trim_jsonl_tail(self.relay_log, self.relay_max_lines)
            self._relay_appends = 0

    async def _broadcast(self, data: dict[str, Any]) -> None:
        """Send one message to every connected socket, ignoring failures."""
        self._mirror_to_relay(data)
        if not self.connected_clients:
            return
        raw = json.dumps(data)
        await asyncio.gather(
            *(client.send(raw) for client in self.connected_clients),
            return_exceptions=True,
        )

    async def _broadcast_presence(self, event: str, agent: str | None = None) -> None:
        """Broadcast a presence update naming who joined or left."""
        await self._broadcast(
            self._system(
                "Presence update",
                msg_type=MessageType.PRESENCE_UPDATE,
                online_agents=self.online_agents(),
                event=event,
                agent=agent,
            )
        )

    async def _send_to_agent(self, agent: str, data: dict[str, Any]) -> bool:
        """Send to a named agent's socket; return whether the send succeeded."""
        websocket = self.agent_sockets.get(agent)
        if websocket is None:
            return False
        try:
            await self._send_json(websocket, data)
            return True
        except Exception:
            return False

    @staticmethod
    def _optional_int(data: dict[str, Any], key: str) -> int | None:
        """Extract an optional integer field from a message, or ``None``.

        Booleans and non-numeric values are treated as absent so a stray ``true``
        is never read as a guard value.

        Parameters
        ----------
        data : dict[str, Any]
            The decoded message.
        key : str
            The field to read.

        Returns
        -------
        int or None
            The integer value, or ``None`` when the field is absent or not numeric.
        """
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    def _drop_waits(self, agent: str) -> None:
        """Remove an agent's wait edge and any waits pointing at it."""
        self._waits.pop(agent, None)
        self._waits = {w: h for w, h in self._waits.items() if h != agent}

    # -- registration + name resolution --------------------------------------

    async def _authorise(self, sender: str, data: dict[str, Any], websocket: Any) -> bool:
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
        if self.authenticator is None or self.socket_agent.get(websocket) is not None:
            return True
        ok, reason = self.authenticator.authenticate(str(data.get("token") or ""), sender)
        if ok:
            return True
        await self._send_json(
            websocket,
            self._system(reason, msg_type=MessageType.AUTH_DENIED, target=sender),
        )
        await websocket.close(code=4010, reason="auth denied")
        return False

    def _warn_if_exposed(self, host: str) -> None:
        """Warn when binding off-loopback without the matching guard configured."""
        if is_loopback_host(host):
            return
        if self.authenticator is None:
            logger.warning(
                "Synapse Hub bound to non-loopback host %r with no token; set an "
                "authenticator (e.g. synapse hub --token ...) before exposing it.",
                host,
            )
        if self.enable_metrics and self.metrics_token is None:
            logger.warning(
                "Synapse Hub metrics enabled on non-loopback host %r with no "
                "--metrics-token; /metrics and /health are unauthenticated.",
                host,
            )

    async def _resolve_sender(
        self, sender: str, websocket: Any, *, takeover: bool = False
    ) -> str | None:
        """Bind a socket to a sender name, enforcing uniqueness.

        When ``takeover`` is set and the name is held by another (possibly stale)
        socket, the holder is evicted and the name rebound to the newcomer — this
        lets a re-arming waiter reclaim its own ``<name>-rx`` from a ghost connection
        without waiting for the keepalive ping to reap it.

        Returns the resolved name, or ``None`` when a name conflict closed the
        socket.
        """
        known_sender = self.socket_agent.get(websocket)
        if known_sender is None:
            owner_ws = self.agent_sockets.get(sender)
            if owner_ws is not None and owner_ws != websocket:
                if takeover:
                    now = self._clock()
                    last = self._last_takeover.get(sender)
                    if last is not None and now - last < self.takeover_cooldown:
                        # An eviction storm — protect the current holder and reject.
                        await websocket.close(code=4014, reason="takeover cooldown")
                        return None
                    self._last_takeover[sender] = now
                    # Detach the stale holder first so its own unregister will not
                    # reclaim the name, then close it and bind the name to the newcomer.
                    self.socket_agent.pop(owner_ws, None)
                    try:
                        await owner_ws.close(code=4010, reason="superseded")
                    except Exception:  # the stale socket may already be half-closed
                        pass
                    self.socket_agent[websocket] = sender
                    return sender
                await self._send_json(
                    websocket,
                    self._system(
                        f"Name '{sender}' is already online from another session. "
                        "Use a unique --name.",
                        msg_type=MessageType.NAME_CONFLICT,
                        target=sender,
                    ),
                )
                await websocket.close(code=4009, reason="name conflict")
                return None
            self.socket_agent[websocket] = sender
            return sender
        if known_sender != sender:
            await self._send_json(
                websocket,
                self._system(
                    f"Sender name switch denied: '{known_sender}' -> '{sender}'. "
                    "Reconnect with a new --name.",
                    msg_type=MessageType.NAME_CONFLICT,
                    target=known_sender,
                ),
            )
            await websocket.close(code=4009, reason="name switch")
            return None
        return known_sender

    @staticmethod
    def _remote_host(websocket: Any) -> str:
        """Return the remote host of ``websocket`` for per-host rate keying.

        Accepts the ``(host, port)`` tuple the websockets server exposes, a bare
        address, or nothing, collapsing to ``"unknown"`` so the per-host bucket
        always has a stable key.
        """
        address = getattr(websocket, "remote_address", None)
        if isinstance(address, (tuple, list)) and address:
            return str(address[0])
        return str(address) if address else "unknown"

    async def handle_message(self, raw_message: str | bytes, websocket: Any) -> None:
        """Parse and route one inbound frame.

        Parameters
        ----------
        raw_message : str or bytes
            The raw frame received from a client socket.
        websocket : Any
            The socket the frame arrived on.
        """
        try:
            data = loads_bounded(raw_message)
        except json.JSONDecodeError:
            await self._send_json(
                websocket, self._system("Malformed JSON.", msg_type=MessageType.ERROR)
            )
            return

        # Charge every frame — heartbeats included — to its remote host before any
        # further work, so one host cannot flood the hub regardless of agent name.
        if self.host_rate_limiter is not None and not self.host_rate_limiter.allow(
            self._remote_host(websocket)
        ):
            await self._send_json(
                websocket, self._system("Host rate limit exceeded.", msg_type=MessageType.ERROR)
            )
            return

        sender = str(data.get("sender") or "").strip() or f"anon-{id(websocket)}"
        target = str(data.get("target") or "all")
        msg_type = str(data.get("type") or MessageType.CHAT).strip().lower()
        payload = str(data.get("payload") or "")

        # Capture whether this socket was already bound before authorising, so a
        # secured hub can send the withheld welcome the moment it first authenticates.
        was_bound = websocket in self.socket_agent
        if not await self._authorise(sender, data, websocket):
            return

        resolved = await self._resolve_sender(
            sender, websocket, takeover=bool(data.get("takeover"))
        )
        if resolved is None:
            return
        sender = resolved
        if self.authenticator is not None and not was_bound:
            await self._send_welcome(websocket)

        self.state.heartbeat(sender)
        is_new_agent = sender not in self.agent_sockets
        self.agent_sockets[sender] = websocket
        if is_new_agent:
            await self._broadcast_presence("joined", sender)
        logger.info("[%s -> %s] (%s): %s", sender, target, msg_type, self._redact_payload(payload))

        if (
            msg_type != MessageType.HEARTBEAT
            and self.rate_limiter is not None
            and not self.rate_limiter.allow(sender)
        ):
            await self._send_json(
                websocket,
                self._system("Rate limit exceeded.", msg_type=MessageType.ERROR, target=sender),
            )
            return

        await self._route(sender, msg_type, data, websocket)

    async def _route(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> None:
        """Dispatch a parsed, sender-resolved message to its handler.

        A duplicate of an already-applied mutation replays its cached response; a
        recognised type is routed through :data:`~synapse_channel.core.handlers.DISPATCH`
        to the matching handler; an unknown type is answered with a private error.
        """
        if await self._maybe_replay_duplicate(msg_type, data, websocket):
            return
        handler = DISPATCH.get(msg_type)
        if handler is None:
            await self._send_to_agent(
                sender,
                self._system(
                    f"Unknown message type '{msg_type}'.",
                    msg_type=MessageType.ERROR,
                    target=sender,
                ),
            )
            return
        await handler(self, sender, data, websocket)

    async def _send_welcome(self, websocket: Any) -> None:
        """Send the welcome frame (roster + connection count) to one socket."""
        await self._send_json(
            websocket,
            self._system(
                "Welcome to Synapse",
                msg_type=MessageType.WELCOME,
                target="self",
                connected_clients=len(self.connected_clients),
                online_agents=self.online_agents(),
            ),
        )

    async def register(self, websocket: Any) -> None:
        """Record a new socket; welcome it now only on an open hub.

        On a secured hub the welcome — which carries the online roster and the
        connection count — is withheld until the socket authenticates (see
        :meth:`handle_message`), so an unauthenticated client never learns who is
        online. An open hub has nothing to gate, so it is welcomed on connect.
        """
        self.connected_clients.add(websocket)
        logger.info("Client connected: %s (total=%d)", id(websocket), len(self.connected_clients))
        if self.authenticator is None:
            await self._send_welcome(websocket)

    async def unregister(self, websocket: Any) -> None:
        """Drop a socket, releasing its agent name and broadcasting departure."""
        self.connected_clients.discard(websocket)
        name = self.socket_agent.pop(websocket, None)
        if name is not None and self.agent_sockets.get(name) == websocket:
            self.agent_sockets.pop(name, None)
            self._drop_waits(name)
            self.capabilities.forget(name)
            if self.rate_limiter is not None:
                self.rate_limiter.forget(name)
            await self._broadcast_presence("left", name)
        logger.info(
            "Client disconnected: %s (total=%d)", id(websocket), len(self.connected_clients)
        )

    async def _authenticate_or_close(self, websocket: Any) -> bool:
        """On a secured hub, process the first frame under the auth deadline.

        Reads one frame within :attr:`auth_timeout`, routes it (which authenticates
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
            first = await asyncio.wait_for(websocket.recv(), timeout=self.auth_timeout)
        except asyncio.TimeoutError:
            await websocket.close(code=4012, reason="auth timeout")
            return False
        except ConnectionClosed:
            return False
        await self.handle_message(first, websocket)
        if websocket not in self.socket_agent:
            # The first frame did not authenticate and bind a name; _authorise may
            # already have closed the socket, so closing again is suppressed.
            with contextlib.suppress(Exception):
                await websocket.close(code=4010, reason="auth required")
            return False
        return True

    async def handler(self, websocket: Any) -> None:
        """Serve one client connection from registration to disconnect.

        On a secured hub the first frame must authenticate within
        :attr:`auth_timeout` before the connection joins the channel (see
        :meth:`_authenticate_or_close`). A separate :attr:`max_unauth_clients` cap
        refuses a new socket (code ``4014``) while that many sockets are still in
        their pre-auth window, so an authentication-stall burst cannot occupy the
        connection table for the whole timeout.
        """
        if len(self.connected_clients) >= self.max_clients:
            await websocket.close(code=4013, reason="hub at capacity")
            return
        if self.authenticator is not None and len(self.unauth_clients) >= self.max_unauth_clients:
            await websocket.close(code=4014, reason="too many unauthenticated connections")
            return
        await self.register(websocket)
        try:
            if self.authenticator is not None:
                self.unauth_clients.add(websocket)
                try:
                    authenticated = await self._authenticate_or_close(websocket)
                finally:
                    self.unauth_clients.discard(websocket)
                if not authenticated:
                    return
            async for raw in websocket:
                await self.handle_message(raw, websocket)
        except ConnectionClosed:
            pass
        finally:
            await self.unregister(websocket)

    def _install_signal_handlers(
        self, loop: asyncio.AbstractEventLoop, stop: asyncio.Event
    ) -> None:
        """Wire ``SIGTERM``/``SIGINT`` to set ``stop`` for a graceful shutdown.

        Best-effort: a platform without signal support (e.g. the Windows proactor loop)
        raises ``NotImplementedError``, which is suppressed — the hub then simply runs
        until its task is cancelled.
        """
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)

    @staticmethod
    def _http_ok(body: bytes, content_type: str) -> Response:
        """Build a ``200 OK`` HTTP response with a body and content type."""
        headers = Headers()
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
        return Response(200, "OK", headers, body)

    @staticmethod
    def _http_unauthorized() -> Response:
        """Build a ``401`` response for a metrics request missing a valid token."""
        body = b"unauthorized\n"
        headers = Headers()
        headers["Content-Type"] = "text/plain; charset=utf-8"
        headers["Content-Length"] = str(len(body))
        headers["WWW-Authenticate"] = 'Bearer realm="synapse-metrics"'
        return Response(401, "Unauthorized", headers, body)

    def _request_metrics_token(self, request: Request) -> str:
        """Extract the metrics token from the request.

        An ``Authorization: Bearer <token>`` header is always honoured. The
        ``?token=<token>`` query form is read only when
        :attr:`metrics_query_token_ok` is set, because a query token can leak into
        access logs, shell history, browser history, and proxy records.
        """
        authorization = request.headers.get("Authorization", "")
        prefix = "Bearer "
        if authorization.startswith(prefix):
            return authorization[len(prefix) :].strip()
        if self.metrics_query_token_ok:
            _, _, query = request.path.partition("?")
            for part in query.split("&"):
                if part.startswith("token="):
                    return part[len("token=") :]
        return ""

    def _metrics_authorised(self, request: Request) -> bool:
        """Return whether a metrics request carries the configured token (if any)."""
        if self.metrics_token is None:
            return True
        return hmac.compare_digest(self._request_metrics_token(request), self.metrics_token)

    def _http_endpoint_response(self, request: Request) -> Response | None:
        """Return the HTTP response for a probe path, or ``None`` to fall through to WS.

        ``/metrics`` renders the Prometheus exposition and ``/health`` a JSON
        liveness document; any other path returns ``None`` so a normal client
        proceeds to the WebSocket handshake. When a :attr:`metrics_token` is set,
        both paths require it and answer ``401`` without it. A query string is
        ignored for routing (but read for the token).
        """
        route = request.path.split("?", 1)[0]
        if route not in ("/metrics", "/health"):
            return None
        if not self._metrics_authorised(request):
            return self._http_unauthorized()
        if route == "/metrics":
            body = render_prometheus(collect_hub_metrics(self)).encode("utf-8")
            return self._http_ok(body, PROMETHEUS_CONTENT_TYPE)
        body = json.dumps(health_snapshot(self)).encode("utf-8")
        return self._http_ok(body, HEALTH_CONTENT_TYPE)

    def _process_request(self, _connection: Any, request: Request) -> Response | None:
        """``websockets`` request hook serving ``/metrics`` and ``/health`` over HTTP.

        Returning a :class:`~websockets.http11.Response` short-circuits the
        WebSocket handshake and sends that HTTP response; returning ``None`` lets
        the connection upgrade to WebSocket as usual. Installed only when
        :attr:`enable_metrics` is set.
        """
        return self._http_endpoint_response(request)

    async def serve(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        """Run the hub's WebSocket server until cancelled.

        With :attr:`enable_metrics` set, the same port also answers HTTP
        ``GET /metrics`` and ``GET /health`` (see :meth:`_process_request`).

        Parameters
        ----------
        host : str, optional
            Bind address. Defaults to :data:`DEFAULT_HOST`.
        port : int, optional
            Bind port. Defaults to :data:`DEFAULT_PORT`.
        """
        self._warn_if_exposed(host)
        stop = asyncio.Event()
        self._install_signal_handlers(asyncio.get_running_loop(), stop)
        async with websockets.serve(
            self.handler,
            host,
            port,
            max_size=self.max_msg_bytes,
            max_queue=DEFAULT_MAX_QUEUE,
            ping_interval=DEFAULT_PING_INTERVAL,
            ping_timeout=DEFAULT_PING_TIMEOUT,
            process_request=self._process_request if self.enable_metrics else None,
        ):
            logger.info("Synapse Hub running on ws://%s:%d", host, port)
            await stop.wait()
