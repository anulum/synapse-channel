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
import json
import logging
import signal
import ssl
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from synapse_channel.core.acl import AclPolicy
from synapse_channel.core.acl_enforcement import authorise_frame, project_of
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability import CapabilityRegistry
from synapse_channel.core.channels import ChannelRegistry
from synapse_channel.core.handlers import DISPATCH
from synapse_channel.core.hub_broadcast import HubBroadcaster
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_exposure import (
    LOOPBACK_HOSTS,
    InsecureBindError,
    exposure_problems,
    guard_exposure,
    is_loopback_host,
)
from synapse_channel.core.hub_http import http_endpoint_response
from synapse_channel.core.hub_ledger_guard import HubLedgerGuard
from synapse_channel.core.hub_relay import RelayMirror
from synapse_channel.core.journal import replay
from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    DEFAULT_MAX_PROGRESS_PER_TASK,
    Blackboard,
)
from synapse_channel.core.message_auth import (
    DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    DEFAULT_SIGNED_MESSAGE_TYPES,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
    SignedEventVerificationResult,
    VerificationResult,
    verify_event_signature,
    verify_frame,
)
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwarder,
    ClaimForwardError,
    ClaimForwardPeer,
    forward_claim,
)
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest
from synapse_channel.core.multihub_serving import MultiHubServingPolicy
from synapse_channel.core.namespace_ownership import NamespaceOwnership, OwnershipOutcome
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import (
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

logger = logging.getLogger("synapse.hub")

__all__ = [
    "InsecureBindError",
    "LOOPBACK_HOSTS",
    "SynapseHub",
    "is_loopback_host",
]

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8876
DEFAULT_MAX_HISTORY = 10000
DEFAULT_MAX_QUEUE = 64
DEFAULT_MAX_FINDINGS_PER_AGENT = 512
"""Maximum durable findings one agent may admit before private rejection."""
DEFAULT_RELAY_MAX_LINES = 5000
DEFAULT_PING_INTERVAL = 15.0
"""Seconds between server keepalive pings, so a dead socket is detected promptly."""
DEFAULT_PING_TIMEOUT = 15.0
"""Seconds to wait for a ping reply before dropping the connection and freeing its name."""
DEFAULT_MAX_CLIENTS = 256
"""Maximum simultaneous connections; a further connect is closed with code 4013.

Sized for a real multi-project fleet rather than a single demo. Each terminal
holds two sockets — its command connection and its persistent ``-rx`` waiter —
and presence daemons add more, so a few dozen active terminals quickly exceed a
low ceiling. When the older default of 64 was hit, every new connection was
rejected with 4013 while already-connected agents kept working, which read as a
silent hub outage to anyone trying to join. Operators on constrained hosts can
still lower this with ``--max-clients``.
"""
DEFAULT_MAX_MSG_BYTES = 1024 * 1024
"""Largest accepted inbound frame (bytes); a larger one is rejected by the transport."""
DEFAULT_TAKEOVER_COOLDOWN = 2.0
"""Seconds a name is protected from a second takeover, to blunt an eviction storm."""
DEFAULT_TAKEOVER_OSCILLATION_WINDOW = 30.0
"""Seconds over which repeated takeovers of one name are counted as an oscillation."""
DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD = 5
"""Takeovers of one name within the window that trip quarantine (two waiters at war)."""
DEFAULT_TAKEOVER_QUARANTINE = 60.0
"""Seconds a thrashing name is pinned to its current owner, refusing all takeovers."""
DEFAULT_AUTH_TIMEOUT = 10.0
"""Seconds a secured hub waits for an authenticated first frame before closing a socket."""
DEFAULT_SHUTDOWN_CLOSE_TIMEOUT = 5.0
"""Seconds allowed for WebSocket close handshakes during hub shutdown."""
MAX_LOG_PAYLOAD = 120
"""Characters of a message payload logged at INFO before it is truncated."""
DEFAULT_COMPACT_HINT_THRESHOLD = 100_000
"""Event-log record count past which the hub logs a one-off ``synapse compact`` hint.

The durable log grows append-only and is never auto-compacted — pruning is safe only
below a sequence the read-side has already consumed, which the hub cannot know. So
instead of silently growing or unsafely trimming, a hub started on a log larger than
this emits a single startup hint to run :class:`compact` manually."""


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
    max_progress_per_author : int, optional
        Maximum progress notes retained for one author on the shared blackboard.
        Defaults to :data:`~synapse_channel.core.ledger.DEFAULT_MAX_PROGRESS_PER_AUTHOR`.
    max_progress_per_task : int, optional
        Maximum progress notes retained for one task id on the shared blackboard.
        Defaults to :data:`~synapse_channel.core.ledger.DEFAULT_MAX_PROGRESS_PER_TASK`.
    max_findings_per_agent : int, optional
        Maximum durable findings one agent may admit before new findings are
        privately rejected. Defaults to :data:`DEFAULT_MAX_FINDINGS_PER_AGENT`.
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
    max_connections_per_host : int or None, optional
        Maximum simultaneous sockets admitted from one remote host. This is
        distinct from the total ``max_clients`` ceiling and the frame-rate
        ``host_rate_limiter``; it counts open sockets, including sockets still in
        their authentication window. ``None`` disables the per-host connection cap.
    shutdown_close_timeout : float, optional
        Seconds allowed for active WebSocket close handshakes after ``SIGTERM`` or
        ``SIGINT`` asks the hub to stop. The timeout is passed to the WebSocket
        server so shutdown stops accepting new sockets and bounds how long active
        close handshakes may delay process exit. Defaults to
        :data:`DEFAULT_SHUTDOWN_CLOSE_TIMEOUT`.
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
    insecure_off_loopback : bool, optional
        Bind a non-loopback host even when it would be reachable unauthenticated.
        Off by default the hub *refuses* such a bind — raising
        :class:`InsecureBindError` rather than only warning — so a bus is never
        accidentally exposed to the network without a token (and, with metrics on,
        a metrics token); set this to downgrade the refusal to a warning.
    per_message_auth_keys : Mapping[str, MessageAuthKey] or list[MessageAuthKey] or None, optional
        HMAC keys accepted for opt-in per-message authentication. ``None`` leaves
        the verifier with no configured keys.
    require_per_message_auth : bool, optional
        When ``True``, selected mutating frames must carry valid per-message
        authentication before they can mutate hub state. Defaults to ``False``.
    per_message_auth_window_seconds : float, optional
        Timestamp window used for signed-frame freshness and replay-cache
        eviction. Defaults to
        :data:`~synapse_channel.core.message_auth.DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS`.
    per_message_auth_replay_capacity : int, optional
        Maximum in-memory nonce entries retained for replay detection.
        Defaults to ``4096``.
    signed_event_trust_bundle : EventSignatureTrustBundle or None, optional
        Ed25519 trust bundle accepted as an alternative signed-event
        verification path when ``require_per_message_auth`` is enabled.
        ``None`` leaves HMAC frame authentication as the only enforcing path.
    multihub_serving_policy : MultiHubServingPolicy or None, optional
        Deny-by-default gate for serving the event log to peer hubs over a multi-hub pull.
        ``None`` (the default) serves every peer; a policy refuses a peer whose live
        certificate it does not trust, mirroring the following side's pull gate.
    namespace_ownership : NamespaceOwnership or None, optional
        Single-authoritative-hub map that routes claims by namespace ownership. ``None`` (the
        default) lets the hub grant claims in every namespace, preserving single-hub behaviour;
        a map refuses a claim whose namespace this hub does not own, fail-closed.
    claim_peers : Mapping[str, ClaimForwardPeer] or None, optional
        How to reach each owning hub to forward a claim it owns, keyed by owning hub id. ``None``
        (the default) forwards nothing: a claim this hub does not own is refused with the owner
        named, as before. With an entry for the resolved owner, a remote-owned claim is forwarded
        to that hub and its verdict relayed to the claimant; an unreachable owner falls back to
        the same refusal, fail-closed.
    claim_forwarder : ClaimForwarder, optional
        The seam that forwards a claim to an owning hub; defaults to the network
        :func:`~synapse_channel.core.multihub_claim_transport.forward_claim`. Injected in tests.
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
        max_progress_per_author: int = DEFAULT_MAX_PROGRESS_PER_AUTHOR,
        max_progress_per_task: int = DEFAULT_MAX_PROGRESS_PER_TASK,
        max_findings_per_agent: int = DEFAULT_MAX_FINDINGS_PER_AGENT,
        compact_hint_threshold: int = DEFAULT_COMPACT_HINT_THRESHOLD,
        authenticator: TokenAuthenticator | None = None,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        max_unauth_clients: int | None = None,
        max_connections_per_host: int | None = None,
        max_msg_bytes: int = DEFAULT_MAX_MSG_BYTES,
        max_claims_per_agent: int = MAX_CLAIMS_PER_AGENT,
        max_offers_per_agent: int = MAX_OFFERS_PER_AGENT,
        max_paths_per_claim: int = MAX_DECLARED_PATHS,
        takeover_cooldown: float = DEFAULT_TAKEOVER_COOLDOWN,
        takeover_oscillation_window: float = DEFAULT_TAKEOVER_OSCILLATION_WINDOW,
        takeover_oscillation_threshold: int = DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD,
        takeover_quarantine: float = DEFAULT_TAKEOVER_QUARANTINE,
        shutdown_close_timeout: float = DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
        enable_metrics: bool = False,
        auth_timeout: float = DEFAULT_AUTH_TIMEOUT,
        metrics_token: str | None = None,
        metrics_query_token_ok: bool = False,
        insecure_off_loopback: bool = False,
        clock: Callable[[], float] | None = None,
        per_message_auth_keys: Mapping[str, MessageAuthKey] | list[MessageAuthKey] | None = None,
        require_per_message_auth: bool = False,
        per_message_auth_window_seconds: float = DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
        per_message_auth_replay_capacity: int = 4096,
        signed_event_trust_bundle: EventSignatureTrustBundle | None = None,
        acl_policy: AclPolicy | None = None,
        require_acl: bool = False,
        multihub_serving_policy: MultiHubServingPolicy | None = None,
        namespace_ownership: NamespaceOwnership | None = None,
        claim_peers: Mapping[str, ClaimForwardPeer] | None = None,
        claim_forwarder: ClaimForwarder = forward_claim,
    ) -> None:
        self.journal = journal
        self.enable_metrics = bool(enable_metrics)
        self.auth_timeout = max(float(auth_timeout), 0.1)
        self.metrics_token = metrics_token or None
        self.metrics_query_token_ok = bool(metrics_query_token_ok)
        self.insecure_off_loopback = bool(insecure_off_loopback)
        self.rate_limiter = rate_limiter
        self.host_rate_limiter = host_rate_limiter
        self.authenticator = authenticator
        if isinstance(per_message_auth_keys, Mapping):
            self.per_message_auth_keys = dict(per_message_auth_keys)
        else:
            self.per_message_auth_keys = {key.key_id: key for key in (per_message_auth_keys or [])}
        self.require_per_message_auth = bool(require_per_message_auth)
        self._message_replay = MessageReplayCache(
            window_seconds=per_message_auth_window_seconds,
            max_entries=per_message_auth_replay_capacity,
        )
        self.signed_event_trust_bundle = signed_event_trust_bundle
        self.acl_policy = acl_policy
        self.require_acl = bool(require_acl)
        self.multihub_serving_policy = multihub_serving_policy
        self.namespace_ownership = namespace_ownership
        self.claim_peers = dict(claim_peers) if claim_peers else None
        self.claim_forwarder = claim_forwarder
        self.channels = ChannelRegistry()
        self.max_msg_bytes = max(int(max_msg_bytes), 1)
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self.clients = HubClientRegistry(
            max_clients=max_clients,
            max_unauth_clients=max_unauth_clients,
            max_connections_per_host=max_connections_per_host,
            takeover_cooldown=takeover_cooldown,
            clock=self._clock,
            takeover_oscillation_window=takeover_oscillation_window,
            takeover_oscillation_threshold=takeover_oscillation_threshold,
            takeover_quarantine=takeover_quarantine,
        )
        self.max_clients = self.clients.max_clients
        self.max_unauth_clients = self.clients.max_unauth_clients
        self.max_connections_per_host = self.clients.max_connections_per_host
        self.takeover_cooldown = self.clients.takeover_cooldown
        self.takeover_oscillation_window = self.clients.takeover_oscillation_window
        self.takeover_oscillation_threshold = self.clients.takeover_oscillation_threshold
        self.takeover_quarantine = self.clients.takeover_quarantine
        self.shutdown_close_timeout = max(float(shutdown_close_timeout), 0.1)
        self.max_history = max(int(max_history), 1)
        self.max_findings_per_agent = max(int(max_findings_per_agent), 1)
        self.compact_hint_threshold = max(1, int(compact_hint_threshold))
        self.relay_log = Path(relay_log) if relay_log else None
        self.relay_max_lines = max(int(relay_max_lines), 1)
        self._relay = RelayMirror(self.relay_log, self.relay_max_lines)
        self._broadcaster = HubBroadcaster(
            self.clients,
            self._relay,
            system=self._system,
            online_agents=self.online_agents,
        )
        self.hub_id = hub_id or f"syn-{uuid.uuid4().hex[:8]}"
        self.connected_clients = self.clients.connected_clients
        self.unauth_clients = self.clients.unauth_clients
        self.agent_sockets = self.clients.agent_sockets
        self.socket_agent = self.clients.socket_agent
        self._waits: dict[str, str] = {}
        self.capabilities = CapabilityRegistry()
        # Ledger-guard seed (message id, idempotency cache, finding quota), resumed
        # from a durable-log replay so the at-most-once and quota guarantees survive a
        # restart, or empty for a purely in-memory hub.
        message_seq = 0
        finding_counts: Mapping[str, int] = {}
        idempotency_seed: tuple[tuple[str, dict[str, Any]], ...] = ()
        if journal is not None:
            replayed = replay(
                journal,
                default_ttl_seconds=default_ttl_seconds,
                max_progress=max_progress,
                max_progress_per_author=max_progress_per_author,
                max_progress_per_task=max_progress_per_task,
                max_claims_per_agent=max_claims_per_agent,
                max_offers_per_agent=max_offers_per_agent,
                max_paths_per_claim=max_paths_per_claim,
            )
            self.state = replayed.state
            self.chat_history = replayed.chat_history[-self.max_history :]
            self.blackboard = replayed.blackboard
            message_seq = replayed.message_seq
            finding_counts = replayed.finding_counts_by_actor
            # Seeded oldest first, the bounded cache keeps the most-recent keys, so a
            # retry after a restart replays the original response instead of re-applying.
            idempotency_seed = tuple(replayed.idempotency)
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
            self.blackboard = Blackboard(
                max_progress=max_progress,
                max_progress_per_author=max_progress_per_author,
                max_progress_per_task=max_progress_per_task,
            )
        self._ledger = HubLedgerGuard(
            max_findings_per_agent=self.max_findings_per_agent,
            journal=self.journal,
            message_seq=message_seq,
            finding_counts=finding_counts,
            idempotency_seed=idempotency_seed,
        )
        # Aliased so existing callers and tests can read the live cache off the hub.
        self._idempotency = self._ledger.idempotency

    # -- helpers --------------------------------------------------------------

    @property
    def _message_seq(self) -> int:
        """Current per-hub message-id high-water mark (owned by the ledger guard)."""
        return self._ledger.message_seq

    def _next_msg_id(self) -> int:
        """Return a strictly increasing per-hub message sequence number."""
        return self._ledger.next_msg_id()

    def _remember(self, data: dict[str, Any], response: dict[str, Any]) -> None:
        """Cache the response of an applied mutation under its idempotency key.

        Thin wrapper over :class:`HubLedgerGuard`, kept because the leasing and
        memory handlers call ``hub._remember`` directly.
        """
        self._ledger.remember(data, response)

    def reserve_finding_slot(self, agent: str) -> tuple[bool, str]:
        """Reserve one durable-finding quota slot for ``agent`` (handler surface)."""
        return self._ledger.reserve_finding_slot(agent)

    async def _maybe_replay_duplicate(
        self, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Replay the cached response for a duplicate mutation, if any.

        Thin wrapper over :class:`HubLedgerGuard`, injecting the hub's per-socket
        send so the guard re-sends the original response to the duplicate's sender.
        """
        return await self._ledger.maybe_replay_duplicate(msg_type, data, websocket, self._send_json)

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
        """Serialise and send one message to a single socket (handler surface)."""
        await self._broadcaster.send_json(websocket, data)

    def _mirror_to_relay(self, data: dict[str, Any]) -> None:
        """Mirror one broadcast to the lite relay log via :class:`RelayMirror`.

        Kept as a thin wrapper because :mod:`synapse_channel.core.messaging` calls
        ``hub._mirror_to_relay`` directly; the append, lite encoding, and bounded
        trimming live in :class:`~synapse_channel.core.hub_relay.RelayMirror`.
        """
        self._relay.mirror(data)

    async def _broadcast(self, data: dict[str, Any]) -> None:
        """Send one message to every connected socket, ignoring failures."""
        await self._broadcaster.broadcast(data)

    async def _broadcast_presence(self, event: str, agent: str | None = None) -> None:
        """Broadcast a presence update naming who joined or left."""
        await self._broadcaster.broadcast_presence(event, agent)

    async def _send_to_agent(self, agent: str, data: dict[str, Any]) -> bool:
        """Send to a named agent's socket; return whether the send succeeded."""
        return await self._broadcaster.send_to_agent(agent, data)

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

    def _exposure_problems(self, host: str) -> list[str]:
        """Return the exposure problems for binding on ``host`` (empty when safe).

        A loopback bind is always safe. Off loopback, a hub with no token — or
        with metrics served but no metrics token — is reachable unauthenticated,
        so each such condition is returned as a human-readable problem.
        """
        return exposure_problems(
            host,
            authenticator=self.authenticator,
            enable_metrics=self.enable_metrics,
            metrics_token=self.metrics_token,
        )

    def _guard_exposure(self, host: str) -> None:
        """Refuse — or, when overridden, warn — before binding an exposed host.

        Off loopback without the matching guard the hub would be reachable
        unauthenticated. By default this raises :class:`InsecureBindError` so the
        bus is never accidentally exposed; with :attr:`insecure_off_loopback` set
        the problems are logged as warnings and the bind proceeds.
        """
        guard_exposure(
            host,
            authenticator=self.authenticator,
            enable_metrics=self.enable_metrics,
            metrics_token=self.metrics_token,
            insecure_off_loopback=self.insecure_off_loopback,
            logger=logger,
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
        return await self.clients.resolve_sender(
            sender,
            websocket,
            takeover=takeover,
            send_json=self._send_json,
            system=self._system,
        )

    @staticmethod
    async def _close_socket(websocket: Any, *, code: int, reason: str) -> None:
        """Close a websocket and wait for close propagation when supported."""
        await HubClientRegistry.close_socket(websocket, code=code, reason=reason)

    @staticmethod
    def _remote_host(websocket: Any) -> str:
        """Return the remote host of ``websocket`` for per-host rate keying.

        Accepts the ``(host, port)`` tuple the websockets server exposes, a bare
        address, or nothing, collapsing to ``"unknown"`` so the per-host bucket
        always has a stable key.
        """
        return HubClientRegistry.remote_host(websocket)

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
        was_bound = self.clients.is_bound(websocket)
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
        is_new_agent = self.clients.set_agent_socket(sender, websocket)
        if is_new_agent:
            await self._broadcast_presence("joined", sender)
        # A channel-scoped frame is audience-restricted, so its body must not land
        # in the hub log either — log the channel id and length, never the content.
        channel_id = str(data.get("channel") or "").strip()
        logged_payload = (
            f"<channel {channel_id!r} body redacted, {len(payload)} chars>"
            if channel_id
            else self._redact_payload(payload)
        )
        logger.info("[%s -> %s] (%s): %s", sender, target, msg_type, logged_payload)

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

        if not await self._verify_per_message_auth(sender, msg_type, data, websocket):
            return

        if not await self._authorise_acl(sender, msg_type, data, websocket):
            return

        if not await self._authorise_claim_ownership(sender, msg_type, data, websocket):
            return

        await self._route(sender, msg_type, data, websocket)

    async def _authorise_acl(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Authorise a mutating frame against the ACL when enforcement is on.

        Returns ``True`` when enforcement is off, no policy is configured, or the
        frame is allowed (including ungated verbs). A denied frame is refused with
        an error naming the rule reason and is not routed.
        """
        if not self.require_acl or self.acl_policy is None:
            return True
        denial = authorise_frame(
            sender=sender, msg_type=msg_type, data=data, policy=self.acl_policy
        )
        if denial is None:
            return True
        logger.warning(
            "ACL denied %s for %s on %s:%s (%s)",
            msg_type,
            sender,
            denial.target.kind,
            denial.target.value,
            denial.reason,
        )
        await self._send_json(
            websocket,
            self._system(
                f"access denied: {denial.permission} on {denial.target.kind}:{denial.target.value}",
                msg_type=MessageType.ERROR,
                target=sender,
                acl_decision=denial.decision,
                acl_reason=denial.reason,
            ),
        )
        return False

    async def _authorise_claim_ownership(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Route a claim by namespace ownership: grant locally, forward to the owner, or refuse.

        Claims are mutual exclusion and are routed by namespace ownership, never merged: a hub
        grants claims only for the namespaces it owns, so two hubs never grant the same scope.
        When a :class:`~synapse_channel.core.namespace_ownership.NamespaceOwnership` map is
        configured, a claim whose namespace — derived from the sender identity exactly as the
        ACL derives it — this hub owns runs the local grant path. A namespace a named peer owns is
        forwarded to that peer when a ``ClaimForwardPeer`` route is configured, and the peer's
        verdict is relayed to the claimant; without a route, or when the owner is unreachable,
        ungoverned, or contested, the claim is refused fail-closed with the owning hub named so
        the caller can route it itself. With no map
        configured the hub owns every namespace it is asked about, preserving single-hub behaviour.

        Returns
        -------
        bool
            ``True`` when the claim may be routed to the local grant path; ``False`` when it was
            handled here — forwarded and its verdict relayed, or refused (a
            :data:`~synapse_channel.core.protocol.MessageType.CLAIM_DENIED` was sent).
        """
        if self.namespace_ownership is None or msg_type != MessageType.CLAIM:
            return True
        namespace = project_of(sender)
        decision = self.namespace_ownership.resolve(namespace)
        if decision.grants_locally:
            return True
        task_id = str(data.get("task_id") or data.get("payload") or "").strip()
        if decision.outcome is OwnershipOutcome.REMOTE and await self._forward_remote_claim(
            sender, namespace, task_id, data, decision.owner_hub_id or "", websocket
        ):
            return False
        logger.warning(
            "Claim refused for %s: namespace %r is %s (owner %s)",
            sender,
            namespace,
            decision.outcome.value,
            decision.owner_hub_id,
        )
        await self._send_json(
            websocket,
            self._system(
                f"claim refused: this hub does not own namespace {namespace!r} "
                f"({decision.outcome.value})",
                msg_type=MessageType.CLAIM_DENIED,
                target=sender,
                task_id=task_id,
                namespace=namespace,
                ownership=decision.outcome.value,
                owner_hub_id=decision.owner_hub_id,
            ),
        )
        return False

    async def _forward_remote_claim(
        self,
        sender: str,
        namespace: str,
        task_id: str,
        data: dict[str, Any],
        owner_hub_id: str,
        websocket: Any,
    ) -> bool:
        """Forward a remote-owned claim to its owning hub and relay the verdict to the claimant.

        The owning hub applies the claim authoritatively and answers with a grant or a denial,
        which is relayed privately to the claimant — a grant carries the authentic lease fields,
        so the client sees the same ``CLAIM_GRANTED`` it would for a local claim.

        Returns
        -------
        bool
            ``True`` when the claim was forwarded and a verdict relayed, so the local grant path
            must not also run. ``False`` when no route is configured for the owner, the task
            carries no id to forward, or the forward failed — leaving the caller to refuse the
            claim and name the owner, fail-closed.
        """
        peer = self.claim_peers.get(owner_hub_id) if self.claim_peers else None
        if peer is None or not task_id:
            return False
        request = ClaimForwardRequest(
            namespace=namespace, claimant=sender, task_id=task_id, claim=data
        )
        try:
            result = await self.claim_forwarder(
                request, uri=peer.uri, local_id=self.hub_id, token=peer.token
            )
        except ClaimForwardError:
            logger.warning(
                "Forwarding claim %r for %s to owner %s failed", task_id, sender, owner_hub_id
            )
            return False
        if result.granted and result.grant is not None:
            await self._send_json(
                websocket,
                self._system(
                    result.detail or f"claim granted by {owner_hub_id}",
                    msg_type=MessageType.CLAIM_GRANTED,
                    target=sender,
                    **result.grant,
                ),
            )
        else:
            await self._send_json(
                websocket,
                self._system(
                    result.detail or "claim refused by the owning hub",
                    msg_type=MessageType.CLAIM_DENIED,
                    target=sender,
                    task_id=task_id,
                    namespace=namespace,
                    owner_hub_id=owner_hub_id,
                ),
            )
        return True

    async def _verify_per_message_auth(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Verify required per-message authentication before mutating state."""
        if not self.require_per_message_auth or msg_type not in DEFAULT_SIGNED_MESSAGE_TYPES:
            return True
        now = time.time()
        if "auth" in data:
            result: VerificationResult | SignedEventVerificationResult = verify_frame(
                data,
                keys=self.per_message_auth_keys,
                replay_cache=self._message_replay,
                now=now,
                required_sender=sender,
            )
            if result is VerificationResult.OK:
                return True
        elif "signature" in data and self.signed_event_trust_bundle is not None:
            result = verify_event_signature(
                data,
                trust_bundle=self.signed_event_trust_bundle,
                now=now,
                required_sender=sender,
                required_project=str(data.get("project") or ""),
            )
            if result is SignedEventVerificationResult.VALID:
                return True
        else:
            result = VerificationResult.MISSING
        await self._send_json(
            websocket,
            self._system(
                f"per-message authentication failed: {result.value}",
                msg_type=MessageType.ERROR,
                target=sender,
                verification_result=result.value,
            ),
        )
        return False

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
        self.clients.add_client(websocket)
        logger.info("Client connected: %s (total=%d)", id(websocket), len(self.connected_clients))
        if self.authenticator is None:
            await self._send_welcome(websocket)

    async def unregister(self, websocket: Any) -> None:
        """Drop a socket, releasing its agent name and broadcasting departure."""
        name = self.clients.drop_client(websocket)
        if name is not None:
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
        if not self.clients.is_bound(websocket):
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
        if self.clients.at_capacity():
            await websocket.close(code=4013, reason="hub at capacity")
            return
        if self.clients.host_at_capacity(websocket):
            await websocket.close(code=4015, reason="too many connections from host")
            return
        if self.authenticator is not None and self.clients.unauthenticated_at_capacity():
            await websocket.close(code=4014, reason="too many unauthenticated connections")
            return
        await self.register(websocket)
        try:
            if self.authenticator is not None:
                self.clients.add_unauthenticated(websocket)
                try:
                    authenticated = await self._authenticate_or_close(websocket)
                finally:
                    self.clients.discard_unauthenticated(websocket)
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

    def _process_request(self, _connection: Any, request: Request) -> Response | None:
        """``websockets`` request hook serving ``/metrics`` and ``/health`` over HTTP.

        Delegates to :func:`~synapse_channel.core.hub_http.http_endpoint_response`,
        which renders the Prometheus exposition for ``/metrics`` and a JSON liveness
        document for ``/health`` (enforcing :attr:`metrics_token` on both), and returns
        ``None`` for any other path so the connection upgrades to WebSocket as usual.
        Returning a :class:`~websockets.http11.Response` short-circuits the handshake
        and sends that HTTP response instead. Installed only when :attr:`enable_metrics`
        is set.
        """
        return http_endpoint_response(self, request)

    async def serve(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        """Run the hub's WebSocket server until cancelled.

        With :attr:`enable_metrics` set, the same port also answers HTTP
        ``GET /metrics`` and ``GET /health`` (see :meth:`_process_request`).

        Parameters
        ----------
        host : str, optional
            Bind address. Defaults to :data:`DEFAULT_HOST`.
        port : int, optional
            Bind port. Defaults to :data:`DEFAULT_PORT`.
        ssl_context : ssl.SSLContext or None, optional
            Server-side TLS context. When supplied, the hub serves native
            ``wss://`` instead of plain ``ws://``.
        """
        self._guard_exposure(host)
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
            close_timeout=self.shutdown_close_timeout,
            process_request=self._process_request if self.enable_metrics else None,
            ssl=ssl_context,
        ):
            scheme = "wss" if ssl_context is not None else "ws"
            logger.info("Synapse Hub running on %s://%s:%d", scheme, host, port)
            await stop.wait()
