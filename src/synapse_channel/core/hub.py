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
import json
import logging
import math
import ssl
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from synapse_channel.core.hub_config import HubConfig

import websockets
from websockets.http11 import Request, Response

from synapse_channel.core.acl import AclPolicy
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability import CapabilityRegistry
from synapse_channel.core.channels import ChannelRegistry
from synapse_channel.core.dead_letter_escalation import DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD
from synapse_channel.core.dead_letter_forwarding import DeadLetterForwarder
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.dead_letters import DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS, DeadLetterLedger
from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.handlers import DISPATCH
from synapse_channel.core.hub_broadcast import HubBroadcaster
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_connection import HubConnection
from synapse_channel.core.hub_counters import HubCounters
from synapse_channel.core.hub_exposure import (
    LOOPBACK_HOSTS,
    InsecureBindError,
    is_loopback_host,
)
from synapse_channel.core.hub_federation_gate import FrameDisposition, HubFederationGate
from synapse_channel.core.hub_frame_gates import HubFrameGates
from synapse_channel.core.hub_http import http_endpoint_response
from synapse_channel.core.hub_ingress import HubIngress
from synapse_channel.core.hub_ledger_guard import HubLedgerGuard
from synapse_channel.core.hub_relay import RelayMirror
from synapse_channel.core.hub_state_seed import seed_hub_state
from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    DEFAULT_MAX_PROGRESS_PER_TASK,
)
from synapse_channel.core.message_auth import (
    DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
)
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwarder,
    ClaimForwardPeer,
    forward_claim,
)
from synapse_channel.core.multihub_serving import (
    MultiHubServingPolicy,
    PeerCertificateSource,
    live_peer_certificate_der,
)
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_approval import RelayApprovalLedger
from synapse_channel.core.operator_relay_forwarding import OperatorRelayForwarding
from synapse_channel.core.operator_relay_transport import (
    OperatorRelayPeer,
    RelayForwarder,
    relay_operator_action,
)
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
)

logger = logging.getLogger("synapse.hub")

# The websockets server logs its connection lifecycle here — a descendant of the
# ``synapse`` logger, so its records reach the app's handler, where
# HandshakeAbortFilter (installed by configure_logging) quiets only the benign
# aborted-handshake tracebacks. websockets creates a per-connection *child* of
# this logger, so the filter must live on the handler (which sees child records),
# not on this logger (whose filters a child record bypasses).
ws_server_logger = logging.getLogger("synapse.hub.ws")

__all__ = [
    "FrameDisposition",
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
    board_task_cap : int or None, optional
        Bound on the tasks served per board snapshot (floored at ``1``):
        live tasks are kept ahead of terminal ones, the newest
        ``updated_at`` wins inside each class when trimming, and a capped
        reply carries ``total_tasks`` and ``truncated`` so a consumer sees
        the bound instead of mistaking the page for the whole plan.
        ``None`` (the default) serves the full board unchanged; the cap
        exists because a long-running fleet's full board eventually
        outgrows a websocket frame.
    max_findings_per_agent : int, optional
        Maximum durable findings one agent may admit before new findings are
        privately rejected. Defaults to :data:`DEFAULT_MAX_FINDINGS_PER_AGENT`.
    compact_hint_threshold : int, optional
        Record count past which a hub started on a durable log emits a one-off
        startup hint to run ``synapse compact`` (the log is never auto-compacted —
        pruning is safe only below a consumed read-side cursor). Clamped up to
        ``1``; set it very high to silence the hint. Defaults to
        :data:`DEFAULT_COMPACT_HINT_THRESHOLD`.
    dead_letter_escalation_threshold : int, optional
        Escalate a dead-letter blackhole every this-many undelivered directed messages to one
        target — the hub broadcasts a one-line notice and journals an audit event when the count
        reaches the threshold and each further multiple, so a growing blackhole becomes an active
        signal rather than a passive snapshot entry. It never re-delivers a message (the ledger
        holds no bodies). ``0`` (the default) disables escalation, leaving the ledger's visibility
        unchanged, and is the default (``DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD``).
    dead_letter_forwarder : DeadLetterForwarder or None, optional
        The seam that hands a dead-letter blackhole signal to the peer hub whose domain owns the
        target, when an escalation fires for a target this hub's namespace-ownership and relay
        routes resolve to a peer. The origin always journals an audit-only forwarding event
        (counts and names, never a message body) and transmits the pointer to the owning hub
        best-effort. Defaults to
        :func:`~synapse_channel.core.dead_letter_forwarding_transport.forward_dead_letter`, the
        websocket transport, so forwarding is wired end-to-end wherever the relay routes it reuses
        are configured; pass ``None`` to record the forwarding intent without transmitting.
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
    relay_peers : Mapping[str, OperatorRelayPeer] or None, optional
        How to reach each owning hub to relay a governed operator action into a namespace it
        owns, keyed by owning hub id — separate from ``claim_peers`` because relaying a
        force-release is more privileged than forwarding a claim. ``None`` (the default)
        forwards no relay: an operator-relay frame for a namespace this hub does not own is
        refused fail-closed. With an entry for the resolved owner, the relay is forwarded to
        that hub and its verdict relayed to the requester, and the origin hub records an
        outbound audit event so the relay is attributable on both hubs.
    relay_forwarder : RelayForwarder, optional
        The seam that relays an operator action to an owning hub; defaults to the network
        :func:`~synapse_channel.core.operator_relay_transport.relay_operator_action`. Injected
        in tests.
    require_relay_reason : bool, optional
        Whether this hub refuses an operator relay that carries no reason. ``False`` (the
        default) records a reason when one is given but does not demand it; a team or production
        hub sets it so every governed cross-hub action leaves an auditable why (reason-required
        receipts).
    require_two_person_relay : bool, optional
        Whether an authorised operator relay needs a second, different operator before it applies.
        ``False`` (the default) applies an authorised relay immediately; a team or production hub
        sets it so a governed cross-hub force-release is recorded pending and carried out only when
        a second operator submits the same action, leaving a two-operator audit trail.
    observed_asserting_hubs : Callable[[str], Iterable[str]] or None, optional
        A runtime feed of the hub ids observed asserting authority over a namespace, consulted
        when resolving ownership so a partition — a peer seen owning a namespace this hub also
        believes it owns — refuses every grant until it is re-established. ``None`` (the default)
        supplies no assertions, so ownership resolves from the static map alone. Build it from a
        follower's observed claims with
        :func:`~synapse_channel.core.multihub_fold.asserting_owners`.
    federation_bundle : FederationBundle or None, optional
        Deny-by-default policy composing a peered remote domain's coordination frames into the
        live authorisation path. ``None`` (the default) leaves the frame path byte-for-byte
        unchanged — every frame is local. With a bundle, a frame whose verified signing key and
        live certificate pin resolve to a peered domain is authorised against that peering's
        bounded scope (composed with mutual TLS, the event signature, and the mapped scope,
        deny-closed) instead of the local ACL; a frame resolving to no peer stays local.
    federation_cert_source : PeerCertificateSource, optional
        Reads the peer's live certificate for the federation gate; defaults to
        :func:`~synapse_channel.core.multihub_serving.live_peer_certificate_der`. Injected in
        tests to exercise the decision without a mutual-TLS handshake.
    federation_offer_path : str or Path or None, optional
        Path to this domain's own federation-bundle material, answered to a peer operator's
        ``synapse federation fetch``. ``None`` (the default) offers nothing — the request is
        answered with an error frame. The file is re-read per request, so the offered
        material rotates without a restart; a fetched offer stays untrusted until the
        fetching operator compares fingerprints out-of-band and imports it explicitly.
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
        board_task_cap: int | None = None,
        max_findings_per_agent: int = DEFAULT_MAX_FINDINGS_PER_AGENT,
        compact_hint_threshold: int = DEFAULT_COMPACT_HINT_THRESHOLD,
        dead_letter_escalation_threshold: int = DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD,
        dead_letter_forwarder: DeadLetterForwarder | None = forward_dead_letter,
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
        relay_peers: Mapping[str, OperatorRelayPeer] | None = None,
        relay_forwarder: RelayForwarder = relay_operator_action,
        require_relay_reason: bool = False,
        require_two_person_relay: bool = False,
        observed_asserting_hubs: Callable[[str], Iterable[str]] | None = None,
        federation_bundle: FederationBundle | None = None,
        federation_cert_source: PeerCertificateSource = live_peer_certificate_der,
        federation_offer_path: str | Path | None = None,
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
        self.relay_peers = dict(relay_peers) if relay_peers else None
        self.relay_forwarder = relay_forwarder
        self.require_relay_reason = bool(require_relay_reason)
        self.require_two_person_relay = bool(require_two_person_relay)
        self.relay_approvals = RelayApprovalLedger()
        self.observed_asserting_hubs = observed_asserting_hubs
        self.federation_bundle = federation_bundle
        self.federation_cert_source = federation_cert_source
        self.federation_offer_path = (
            Path(federation_offer_path) if federation_offer_path is not None else None
        )
        self._federation_gate = HubFederationGate(
            federation_bundle,
            cert_source=federation_cert_source,
            require_per_message_auth=self.require_per_message_auth,
            signed_event_trust=signed_event_trust_bundle is not None,
            system=self._system,
            send_json=self._send_json,
        )
        self.channels = ChannelRegistry()
        self.max_msg_bytes = max(int(max_msg_bytes), 1)
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self.counters = HubCounters()
        self.clients = HubClientRegistry(
            counters=self.counters,
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
        self.dead_letter_escalation_threshold = max(0, int(dead_letter_escalation_threshold))
        self.dead_letter_forwarder = dead_letter_forwarder
        self.board_task_cap = max(1, int(board_task_cap)) if board_task_cap is not None else None
        self.relay_log = Path(relay_log) if relay_log else None
        self.relay_max_lines = max(int(relay_max_lines), 1)
        self.dead_letters = DeadLetterLedger(max_age_seconds=DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS)
        self._relay = RelayMirror(self.relay_log, self.relay_max_lines)
        self._broadcaster = HubBroadcaster(
            self.clients,
            self._relay,
            system=self._system,
            online_agents=self.online_agents,
        )
        self.hub_id = hub_id or f"syn-{uuid.uuid4().hex[:8]}"
        # A fingerprint of the configuration posture this hub was built from,
        # for a cockpit's pinning indicator. Empty for an ad-hoc construction;
        # :meth:`from_config` sets it from the grouped record (the production path).
        self.config_epoch = ""
        self._ingress = HubIngress(
            self.clients,
            authenticator=self.authenticator,
            enable_metrics=self.enable_metrics,
            metrics_token=self.metrics_token,
            metrics_query_token_ok=self.metrics_query_token_ok,
            insecure_off_loopback=self.insecure_off_loopback,
            send_json=self._send_json,
            system=self._system,
        )
        self.connected_clients = self.clients.connected_clients
        self.unauth_clients = self.clients.unauth_clients
        self.agent_sockets = self.clients.agent_sockets
        self.agent_roles = self.clients.agent_roles
        self.socket_agent = self.clients.socket_agent
        self._waits: dict[str, str] = {}
        self.capabilities = CapabilityRegistry()
        self._connection = HubConnection(
            self.clients,
            self.capabilities,
            authenticator=self.authenticator,
            auth_timeout=self.auth_timeout,
            rate_limiter=self.rate_limiter,
            handle_message=self.handle_message,
            send_json=self._send_json,
            system=self._system,
            online_agents=self.online_agents,
            broadcast_presence=self._broadcast_presence,
            drop_waits=self._drop_waits,
        )
        self._frame_gates = HubFrameGates(
            require_per_message_auth=self.require_per_message_auth,
            per_message_auth_keys=self.per_message_auth_keys,
            message_replay=self._message_replay,
            signed_event_trust_bundle=self.signed_event_trust_bundle,
            require_acl=self.require_acl,
            acl_policy=self.acl_policy,
            namespace_ownership=self.namespace_ownership,
            observed_asserting_hubs=self.observed_asserting_hubs,
            claim_peers=self.claim_peers,
            claim_forwarder=self.claim_forwarder,
            hub_id=self.hub_id,
            send_json=self._send_json,
            system=self._system,
        )
        self._relay_forwarding = OperatorRelayForwarding(
            namespace_ownership=self.namespace_ownership,
            relay_peers=self.relay_peers,
            relay_forwarder=self.relay_forwarder,
            observed_asserting_hubs=self.observed_asserting_hubs,
            hub_id=self.hub_id,
            journal=self.journal,
            send_json=self._send_json,
            system=self._system,
        )
        # Resume durable state from the log — leases, chat history, the blackboard,
        # and the ledger-guard seed (message id, finding quota, idempotency cache) —
        # so a restart continues where it left off, or start empty with no journal.
        seeded = seed_hub_state(
            journal,
            default_ttl_seconds=default_ttl_seconds,
            max_history=self.max_history,
            max_progress=max_progress,
            max_progress_per_author=max_progress_per_author,
            max_progress_per_task=max_progress_per_task,
            max_claims_per_agent=max_claims_per_agent,
            max_offers_per_agent=max_offers_per_agent,
            max_paths_per_claim=max_paths_per_claim,
            compact_hint_threshold=self.compact_hint_threshold,
        )
        self.state = seeded.state
        self.chat_history = seeded.chat_history
        self.blackboard = seeded.blackboard
        self._ledger = HubLedgerGuard(
            max_findings_per_agent=self.max_findings_per_agent,
            journal=self.journal,
            message_seq=seeded.message_seq,
            finding_counts=seeded.finding_counts,
            idempotency_seed=seeded.idempotency_seed,
        )
        # Aliased so existing callers and tests can read the live cache off the hub.
        self._idempotency = self._ledger.idempotency

    @classmethod
    def from_config(cls, config: HubConfig | None = None) -> SynapseHub:
        """Construct a hub from a grouped :class:`HubConfig` record.

        Parameters
        ----------
        config : HubConfig or None, optional
            The grouped configuration; ``None`` builds the same hub as a bare
            ``SynapseHub()``. The record flattens to exactly this class's
            keyword parameters (pinned by contract tests), so the two
            construction paths cannot diverge.
        """
        from synapse_channel.core.hub_config import HubConfig, config_fingerprint

        resolved = config if config is not None else HubConfig()
        hub = cls(**resolved.to_kwargs())
        hub.config_epoch = config_fingerprint(resolved)
        return hub

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

    def set_agent_roles(self, name: str, roles: tuple[str, ...]) -> None:
        """Bind the roles an agent answers to, as declared on its registration heartbeat."""
        self.clients.set_roles(name, roles)

    def roles_of(self, name: str) -> tuple[str, ...]:
        """Return the roles ``name`` currently answers to (empty tuple if none)."""
        return self.clients.roles_of(name)

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
        is never read as a guard value; a non-finite float (``inf``/``nan``, which a
        JSON ``1e400`` decodes to) is treated as absent too, since ``int()`` of it
        raises and would otherwise escape the frame handler as an unhandled error.

        Parameters
        ----------
        data : dict[str, Any]
            The decoded message.
        key : str
            The field to read.

        Returns
        -------
        int or None
            The integer value, or ``None`` when the field is absent, not numeric,
            or a non-finite float.
        """
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return int(value)

    def _drop_waits(self, agent: str) -> None:
        """Remove an agent's wait edge and any waits pointing at it."""
        self._waits.pop(agent, None)
        self._waits = {w: h for w, h in self._waits.items() if h != agent}

    # -- registration + name resolution --------------------------------------

    async def _authorise(self, sender: str, data: dict[str, Any], websocket: Any) -> bool:
        """Gate the first message from a socket on the shared-secret token.

        Thin wrapper over :meth:`~synapse_channel.core.hub_ingress.HubIngress.authorise`,
        kept because :meth:`handle_message` calls ``self._authorise`` directly.
        """
        return await self._ingress.authorise(sender, data, websocket)

    def _exposure_problems(self, host: str) -> list[str]:
        """Return the exposure problems for binding on ``host`` (empty when safe).

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_ingress.HubIngress.exposure_problems`, kept
        because operator tooling and tests read ``hub._exposure_problems`` directly.
        """
        return self._ingress.exposure_problems(host)

    def _guard_exposure(self, host: str) -> None:
        """Refuse — or, when overridden, warn — before binding an exposed host.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_ingress.HubIngress.guard_exposure`, kept
        because :meth:`serve` and tests call ``hub._guard_exposure`` directly.
        """
        self._ingress.guard_exposure(host)

    async def _resolve_sender(
        self, sender: str, websocket: Any, *, takeover: bool = False
    ) -> str | None:
        """Bind a socket to a sender name, enforcing uniqueness.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_ingress.HubIngress.resolve_sender`, kept
        because :meth:`handle_message` calls ``self._resolve_sender`` directly.
        """
        return await self._ingress.resolve_sender(sender, websocket, takeover=takeover)

    @staticmethod
    async def _close_socket(websocket: Any, *, code: int, reason: str) -> None:
        """Close a websocket and wait for close propagation when supported.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_ingress.HubIngress.close_socket`, kept as a
        class-callable staticmethod because tests invoke ``SynapseHub._close_socket``.
        """
        await HubIngress.close_socket(websocket, code=code, reason=reason)

    @staticmethod
    def _remote_host(websocket: Any) -> str:
        """Return the remote host of ``websocket`` for per-host rate keying.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_ingress.HubIngress.remote_host`, kept as a
        class-callable staticmethod because :meth:`handle_message` and tests invoke
        ``SynapseHub._remote_host``.
        """
        return HubIngress.remote_host(websocket)

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
        self.dead_letters.clear(sender)
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
            self.counters.rate_limited += 1
            await self._send_json(
                websocket,
                self._system("Rate limit exceeded.", msg_type=MessageType.ERROR, target=sender),
            )
            return

        if not await self._verify_per_message_auth(sender, msg_type, data, websocket):
            self.counters.auth_failures += 1
            return

        disposition = await self._authorise_federation(sender, msg_type, data, websocket)
        if disposition is FrameDisposition.DENY:
            self.counters.federation_denied += 1
            return
        if disposition is FrameDisposition.ALLOW_CROSS_DOMAIN:
            await self._route(sender, msg_type, data, websocket)
            return

        if not await self._authorise_acl(sender, msg_type, data, websocket):
            return

        if not await self._authorise_claim_ownership(sender, msg_type, data, websocket):
            return

        if not await self._route_operator_relay(sender, msg_type, data, websocket):
            return

        await self._route(sender, msg_type, data, websocket)

    async def _authorise_federation(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> FrameDisposition:
        """Classify a frame as local or cross-domain and authorise the cross-domain case.

        Kept as a thin wrapper so the frame handler and its tests keep one gate entry
        point on the hub; the resolution, deny-closed composition, and denial reply live
        in :class:`~synapse_channel.core.hub_federation_gate.HubFederationGate`.
        """
        return await self._federation_gate.authorise(sender, msg_type, data, websocket)

    def _warn_unresolved_federation(
        self, sender: str, msg_type: str, key_id: str, pin: str
    ) -> None:
        """Log a misconfiguration signal when a signed, pinned frame resolves to no domain.

        Kept as a thin wrapper over
        :meth:`~synapse_channel.core.hub_federation_gate.HubFederationGate.warn_unresolved`,
        which owns the diagnosis and the operator-facing warning.
        """
        self._federation_gate.warn_unresolved(sender, msg_type, key_id, pin)

    async def _authorise_acl(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Authorise a mutating frame against the ACL when enforcement is on.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_frame_gates.HubFrameGates.authorise_acl`, kept
        because :meth:`handle_message` calls ``self._authorise_acl`` directly.
        """
        return await self._frame_gates.authorise_acl(sender, msg_type, data, websocket)

    async def _authorise_claim_ownership(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Route a claim by namespace ownership: grant locally, forward, or refuse.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_frame_gates.HubFrameGates.authorise_claim_ownership`,
        kept because :meth:`handle_message` calls ``self._authorise_claim_ownership`` directly.
        """
        return await self._frame_gates.authorise_claim_ownership(sender, msg_type, data, websocket)

    async def _route_operator_relay(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Route an operator-relay frame by ownership: apply locally, forward, or refuse.

        Thin wrapper over
        :meth:`~synapse_channel.core.operator_relay_forwarding.OperatorRelayForwarding.route`,
        kept because :meth:`handle_message` calls ``self._route_operator_relay`` directly. Returns
        ``True`` when the frame may proceed to the local serving handler (this hub owns the
        namespace), ``False`` when it was forwarded to the owner or refused fail-closed.
        """
        return await self._relay_forwarding.route(sender, msg_type, data, websocket)

    async def _verify_per_message_auth(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Verify required per-message authentication before mutating state.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_frame_gates.HubFrameGates.verify_per_message_auth`,
        kept because :meth:`handle_message` calls ``self._verify_per_message_auth`` directly.
        """
        return await self._frame_gates.verify_per_message_auth(sender, msg_type, data, websocket)

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
        """Send the welcome frame (roster + connection count) to one socket.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_connection.HubConnection.send_welcome`, kept
        because :meth:`handle_message` sends the withheld welcome on first auth.
        """
        await self._connection.send_welcome(websocket)

    async def handler(self, websocket: Any) -> None:
        """Serve one client connection from registration to disconnect.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_connection.HubConnection.handler`, kept as
        the entry point :meth:`serve` hands to ``websockets.serve``.
        """
        await self._connection.handler(websocket)

    def _install_signal_handlers(
        self, loop: asyncio.AbstractEventLoop, stop: asyncio.Event
    ) -> None:
        """Wire ``SIGTERM``/``SIGINT`` to set ``stop`` for a graceful shutdown.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_connection.HubConnection.install_signal_handlers`,
        kept because :meth:`serve` and tests call ``hub._install_signal_handlers``.
        """
        HubConnection.install_signal_handlers(loop, stop)

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
            logger=ws_server_logger,
        ):
            scheme = "wss" if ssl_context is not None else "ws"
            logger.info("Synapse Hub running on %s://%s:%d", scheme, host, port)
            await stop.wait()
