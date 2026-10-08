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
import dataclasses
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

from websockets.asyncio.server import serve
from websockets.http11 import Request, Response

from synapse_channel.core.acl import (
    OBSERVE,
    ROLE_CLAIM,
    WOULD_ALLOW,
    AclPolicy,
    Target,
    evaluate_access,
)
from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.agent_liveness import (
    DEFAULT_RECIPIENT_LIVENESS_WINDOW,
    DEFAULT_WAITER_LIVENESS_WINDOW,
    DEFAULT_WARN_STALE_RECIPIENTS,
    RecipientLiveness,
)
from synapse_channel.core.at_rest_guard import guard_at_rest
from synapse_channel.core.atomic_operations import (
    AtomicExecution,
    OperationDraft,
    OperationRecord,
    canonical_request_digest,
    idempotency_conflict_response,
)
from synapse_channel.core.attachment_serving import AttachmentServingPolicy
from synapse_channel.core.attachment_store import AttachmentStore
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability import CapabilityRegistry
from synapse_channel.core.capability_card_trust import CapabilityCardTrustBundle
from synapse_channel.core.channels import ChannelRegistry
from synapse_channel.core.chat_dedupe import ChatDedupe
from synapse_channel.core.claim_holder_presence import ClaimHolderPresence
from synapse_channel.core.dark_seat import DarkSeatMonitor
from synapse_channel.core.dead_letter_escalation import DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD
from synapse_channel.core.dead_letter_forwarding import DeadLetterForwarder
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.dead_letters import DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS, DeadLetterLedger
from synapse_channel.core.deadlock import prune_waits
from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.delivery_registration import bind_delivery_registration
from synapse_channel.core.durable_ingress import DurableIngressQuota
from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.handlers import DISPATCH
from synapse_channel.core.hub_broadcast import HubBroadcaster
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_connection import HubConnection
from synapse_channel.core.hub_counters import HubCounters
from synapse_channel.core.hub_defaults import (
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_COMPACT_HINT_THRESHOLD,
    DEFAULT_HOST,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_CONNECTIONS_PER_HOST,
    DEFAULT_MAX_FINDINGS_PER_AGENT,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_MSG_BYTES,
    DEFAULT_MAX_QUEUE,
    DEFAULT_PING_INTERVAL,
    DEFAULT_PING_TIMEOUT,
    DEFAULT_PORT,
    DEFAULT_RELAY_MAX_LINES,
    DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
    DEFAULT_TAKEOVER_COOLDOWN,
    DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD,
    DEFAULT_TAKEOVER_OSCILLATION_WINDOW,
    DEFAULT_TAKEOVER_QUARANTINE,
    MAX_LOG_PAYLOAD,
)
from synapse_channel.core.hub_exposure import (
    LOOPBACK_HOSTS,
    InsecureBindError,
    is_loopback_host,
)
from synapse_channel.core.hub_federation_gate import FrameDisposition, HubFederationGate
from synapse_channel.core.hub_frame_gates import HubFrameGates
from synapse_channel.core.hub_http import http_endpoint_response
from synapse_channel.core.hub_identity_gate import HubIdentityGate
from synapse_channel.core.hub_ingress import HubIngress
from synapse_channel.core.hub_journal_recovery_gate import HubJournalRecoveryGate
from synapse_channel.core.hub_ledger_guard import FindingQuota, HubLedgerGuard
from synapse_channel.core.hub_liveness import HubLivenessView
from synapse_channel.core.hub_relay import RelayMirror
from synapse_channel.core.hub_state_seed import seed_hub_state
from synapse_channel.core.identity_enrollments import (
    DEFAULT_ENROLLMENT_RATE,
    DEFAULT_ENROLLMENT_WINDOW_SECONDS,
    EnrollmentRateLimiter,
    load_enrolled_keys,
    merge_enrolled_keys,
)
from synapse_channel.core.identity_pins import IdentityPinStore
from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    DEFAULT_MAX_PROGRESS_PER_TASK,
)
from synapse_channel.core.mailbox_pending import MailboxPendingTracker
from synapse_channel.core.merkle_checkpoint import (
    DEFAULT_CHECKPOINT_INTERVAL,
    LiveCheckpoint,
    MerkleCheckpointStore,
    checkpoint_path_for,
)
from synapse_channel.core.message_auth import (
    DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
)
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.message_forward_ledger import MessageForwardLedger
from synapse_channel.core.message_forward_origin import DEFAULT_FORWARD_TTL_SECONDS
from synapse_channel.core.message_forward_transport import (
    MessageForwarder,
    MessageForwardPeer,
    forward_message,
)
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwarder,
    ClaimForwardPeer,
    forward_claim,
)
from synapse_channel.core.multihub_serving import (
    MultiHubServingPolicy,
    PeerCertificateSource,
    check_identity_grants,
    live_peer_certificate_der,
)
from synapse_channel.core.name_ownership import DEFAULT_LEASE_OFFLINE_TTL
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.numeric_coercion import safe_float, safe_int
from synapse_channel.core.operator_relay_forwarding import OperatorRelayForwarding
from synapse_channel.core.operator_relay_transport import (
    OperatorRelayPeer,
    RelayForwarder,
    relay_operator_action,
)
from synapse_channel.core.pending_receipts import PendingReceipts
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_request import (
    parse_protected_write_request,
    protected_write_operation_key,
)
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
    recheck_authenticated_protected_request,
)
from synapse_channel.core.protocol import (
    MessageType,
    loads_bounded,
    read_protocol_version,
    system_message,
)
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.role_grants import RoleGrants
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.spend_ledger import SpendLedger
from synapse_channel.core.state import (
    MAX_CLAIMS_PER_AGENT,
    MAX_OFFERS_PER_AGENT,
)
from synapse_channel.core.state_transaction import SerializedStateMutationActor
from synapse_channel.core.terminal_text import terminal_text

logger = logging.getLogger("synapse.hub")

# The websockets server logs its connection lifecycle here — a descendant of the
# ``synapse`` logger, so its records reach the app's handler, where
# HandshakeAbortFilter (installed by configure_logging) quiets only the benign
# aborted-handshake tracebacks. websockets creates a per-connection *child* of
# this logger, so the filter must live on the handler (which sees child records),
# not on this logger (whose filters a child record bypasses).
ws_server_logger = logging.getLogger("synapse.hub.ws")

__all__ = [
    "DEFAULT_AUTH_TIMEOUT",
    "DEFAULT_COMPACT_HINT_THRESHOLD",
    "DEFAULT_HOST",
    "DEFAULT_MAX_CLIENTS",
    "DEFAULT_MAX_CONNECTIONS_PER_HOST",
    "DEFAULT_MAX_FINDINGS_PER_AGENT",
    "DEFAULT_MAX_HISTORY",
    "DEFAULT_MAX_MSG_BYTES",
    "DEFAULT_MAX_QUEUE",
    "DEFAULT_PING_INTERVAL",
    "DEFAULT_PING_TIMEOUT",
    "DEFAULT_PORT",
    "DEFAULT_RELAY_MAX_LINES",
    "DEFAULT_SHUTDOWN_CLOSE_TIMEOUT",
    "DEFAULT_TAKEOVER_COOLDOWN",
    "DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD",
    "DEFAULT_TAKEOVER_OSCILLATION_WINDOW",
    "DEFAULT_TAKEOVER_QUARANTINE",
    "FrameDisposition",
    "InsecureBindError",
    "LOOPBACK_HOSTS",
    "MAX_LOG_PAYLOAD",
    "SynapseHub",
    "is_loopback_host",
]


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
    durable_ingress_quota : DurableIngressQuota or None, optional
        When given, each accepted chat is charged to the connection's
        server-derived quota principal (events and serialized chat-frame bytes in a sliding
        window). Over-quota chats are refused before history or journal growth so
        one principal cannot fill the durable log; ``None`` disables the bound.
    max_history : int, optional
        Maximum chat messages retained in memory; the oldest are dropped beyond
        this bound so history cannot grow without limit. The durable log (when a
        journal is attached) still records every message. Defaults to
        :data:`DEFAULT_MAX_HISTORY`.
    relay_log : str or pathlib.Path or None, optional
        When given, every broadcast message is also mirrored to this newline-
        delimited log in the compact lite format (see
        :func:`~synapse_channel.core.relay.encode_lite`), so a token-budgeted agent
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
        Seconds to wait for a name-binding first frame before closing the socket
        (code ``4012``). On a secured hub the first frame must also authenticate
        and the roster is withheld until then; on an open hub the welcome is still
        sent on connect, but an idle socket that never registers is reaped so it
        cannot hold a connection or per-host slot. Defaults to
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
        their first-frame window. Defaults to
        :data:`DEFAULT_MAX_CONNECTIONS_PER_HOST`. ``None`` disables the per-host
        connection cap.
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
    insecure_plaintext_at_rest : bool, optional
        Bind a non-loopback host with a plaintext ``--db`` event store. Off by
        default the hub *refuses* such a bind — raising :class:`AtRestBindError` —
        so the durable coordination log never sits unencrypted on an exposed
        host's disk; encrypt the store (``--db-key-file``) or set this to downgrade
        the refusal to a warning. Loopback binds and encrypted stores are
        unaffected.
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
    capability_card_trust_bundle : CapabilityCardTrustBundle or None, optional
        Separate Ed25519 trust and bounded lifecycle state used only to label
        capability-card advertisements. Verification stays advisory and default-off.
    multihub_serving_policy : MultiHubServingPolicy or None, optional
        Deny-by-default gate for serving the event log to peer hubs over a multi-hub pull.
        ``None`` (the default) refuses every peer. An explicit policy serves only a peer
        whose sender grant and live certificate it trusts, mirroring the following side's
        fail-closed pull gate. A grant naming an ``identity_key_id`` is proven instead by a
        registration this hub verified under that key; the hub then needs
        ``require_identity_binding`` and a trust bundle binding that key to the sender, or
        construction raises ``ValueError``.
    attachment_serving_policy : AttachmentServingPolicy or None, optional
        Source-owned exact recipient hub, scope, digest and expiry grants. Reloaded
        before every peer read. Requires attachments and a peer serving policy.
    spend_ledger : SpendLedger or None, optional
        The owner-only ledger that makes this hub the owner of shared pools (F02).
        ``None`` (the default) refuses every ``spend_request`` uniformly. A peer is
        served only when ``multihub_serving_policy`` authorises it.
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
    message_peers : Mapping[str, MessageForwardPeer] or None, optional
        Peer hubs this hub forwards messages to, keyed by hub id. A chat or delivery addressed
        to ``PROJECT/seat@HUB_ID`` is forwarded to ``HUB_ID`` when it is listed here and
        refused otherwise. ``None`` (the default) forwards nothing. Receiving forwards is
        governed separately by ``multihub_serving_policy``.
    message_forwarder : MessageForwarder, optional
        The seam that forwards one message; defaults to the network
        :func:`~synapse_channel.core.message_forward_transport.forward_message`. Injected in
        tests.
    message_forward_ttl : float, optional
        Seconds an unanswered forwarded chat is retried before it expires; at least 1.
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
    anti_rollback_checkpoint : bool, optional
        When ``True`` (the default) and a journal is attached, the hub verifies the
        durable log against its persisted Merkle checkpoint BEFORE serving — a
        truncated tail or a rewritten prefix raises
        :class:`~synapse_channel.core.merkle_checkpoint.AntiRollbackError` at startup
        instead of restarting silently — then anchors the current state as the
        newest hash-chained checkpoint link.
    checkpoint_store_path : str or Path or None, optional
        Override for the checkpoint database location; defaults to
        ``<journal path>.checkpoint.db`` beside the event store. The checkpoint
        store must live outside the log it attests.
    checkpoint_interval : float, optional
        Seconds between live anchors while the hub serves (default
        :data:`~synapse_channel.core.merkle_checkpoint.DEFAULT_CHECKPOINT_INTERVAL`).
        This is the declared window: writes newer than the latest anchor could be
        cut from the log undetected after a crash; a clean shutdown anchors at once
        and closes the checkpoint store. Must be a positive finite number.
    protected_write_policies : Mapping or None, optional
        Retained enrollment replay policies for protected reservations. Omitting
        them refuses protected history; supplying them does not enable dispatch.
    """

    def __init__(
        self,
        *,
        default_ttl_seconds: float = 3600.0,
        hub_id: str | None = None,
        journal: EventStore | None = None,
        attachment_store: AttachmentStore | None = None,
        attachment_serving_policy: AttachmentServingPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        host_rate_limiter: RateLimiter | None = None,
        durable_ingress_quota: DurableIngressQuota | None = None,
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
        max_connections_per_host: int | None = DEFAULT_MAX_CONNECTIONS_PER_HOST,
        max_msg_bytes: int = DEFAULT_MAX_MSG_BYTES,
        max_claims_per_agent: int = MAX_CLAIMS_PER_AGENT,
        max_offers_per_agent: int = MAX_OFFERS_PER_AGENT,
        max_paths_per_claim: int = MAX_DECLARED_PATHS,
        takeover_cooldown: float = DEFAULT_TAKEOVER_COOLDOWN,
        takeover_oscillation_window: float = DEFAULT_TAKEOVER_OSCILLATION_WINDOW,
        takeover_oscillation_threshold: int = DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD,
        takeover_quarantine: float = DEFAULT_TAKEOVER_QUARANTINE,
        lease_offline_ttl: float = DEFAULT_LEASE_OFFLINE_TTL,
        shutdown_close_timeout: float = DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
        enable_metrics: bool = False,
        auth_timeout: float = DEFAULT_AUTH_TIMEOUT,
        metrics_token: str | None = None,
        metrics_query_token_ok: bool = False,
        allowed_origins: tuple[str, ...] | list[str] = (),
        advertised_host: str | None = None,
        insecure_off_loopback: bool = False,
        insecure_plaintext_at_rest: bool = False,
        clock: Callable[[], float] | None = None,
        protected_write_policies: Mapping[str, ProtectedAdmissionReplayPolicy] | None = None,
        per_message_auth_keys: Mapping[str, MessageAuthKey] | list[MessageAuthKey] | None = None,
        require_per_message_auth: bool = False,
        per_message_auth_window_seconds: float = DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
        per_message_auth_replay_capacity: int = 4096,
        per_message_auth_replay_store: DurableMessageAuthReplayStore | None = None,
        per_message_auth_sequence_floor_mode: SequenceFloorMode | str = SequenceFloorMode.OFF,
        signed_event_trust_bundle: EventSignatureTrustBundle | None = None,
        capability_card_trust_bundle: CapabilityCardTrustBundle | None = None,
        acl_policy: AclPolicy | None = None,
        require_acl: bool = False,
        role_grants: RoleGrants | None = None,
        require_role_claim: bool = False,
        require_fencing_epoch: bool = False,
        identity_trust_bundle: EventSignatureTrustBundle | None = None,
        require_identity_binding: bool = False,
        identity_pin_path: str | Path | None = None,
        identity_enrollment_path: str | Path | None = None,
        identity_enrollment_namespaces: tuple[str, ...] = (),
        identity_enrollment_rate: int = DEFAULT_ENROLLMENT_RATE,
        identity_enrollment_window_seconds: float = DEFAULT_ENROLLMENT_WINDOW_SECONDS,
        private_directed_messages: bool = False,
        warn_stale_recipients: bool = DEFAULT_WARN_STALE_RECIPIENTS,
        recipient_liveness_window: float = DEFAULT_RECIPIENT_LIVENESS_WINDOW,
        waiter_liveness_window: float = DEFAULT_WAITER_LIVENESS_WINDOW,
        multihub_serving_policy: MultiHubServingPolicy | None = None,
        spend_ledger: SpendLedger | None = None,
        namespace_ownership: NamespaceOwnership | None = None,
        claim_peers: Mapping[str, ClaimForwardPeer] | None = None,
        claim_forwarder: ClaimForwarder = forward_claim,
        relay_peers: Mapping[str, OperatorRelayPeer] | None = None,
        relay_forwarder: RelayForwarder = relay_operator_action,
        message_peers: Mapping[str, MessageForwardPeer] | None = None,
        message_forwarder: MessageForwarder = forward_message,
        message_forward_ttl: float = DEFAULT_FORWARD_TTL_SECONDS,
        require_relay_reason: bool = False,
        require_two_person_relay: bool = False,
        observed_asserting_hubs: Callable[[str], Iterable[str]] | None = None,
        federation_bundle: FederationBundle | None = None,
        federation_cert_source: PeerCertificateSource = live_peer_certificate_der,
        federation_offer_path: str | Path | None = None,
        anti_rollback_checkpoint: bool = True,
        checkpoint_store_path: str | Path | None = None,
        checkpoint_interval: float = DEFAULT_CHECKPOINT_INTERVAL,
    ) -> None:
        if attachment_store is not None and not (
            authenticator is not None
            and require_identity_binding
            and identity_trust_bundle is not None
            and require_per_message_auth
            and per_message_auth_keys
            and per_message_auth_replay_store is not None
            and require_acl
            and acl_policy is not None
            and role_grants is not None
            and journal is not None
        ):
            raise ValueError(
                "attachments require token, bound identity, durable signed frames, "
                "ACL, roles, and journal"
            )
        if attachment_serving_policy is not None:
            if attachment_store is None or multihub_serving_policy is None:
                raise ValueError(
                    "attachment recipient policy requires attachments and peer serving policy"
                )
            attachment_serving_policy.load()
        self.attachment_serving_policy = attachment_serving_policy
        self.attachment_store = attachment_store
        self.journal = journal
        interval = float(checkpoint_interval)
        if not (math.isfinite(interval) and interval > 0.0):
            raise ValueError("checkpoint_interval must be a positive finite number of seconds")
        self.checkpoint_interval = interval
        self._checkpoint_path: Path | None = None
        self._live_checkpoint: LiveCheckpoint | None = None
        if (
            anti_rollback_checkpoint
            and isinstance(journal, EventStore)
            and journal.path != ":memory:"
        ):
            self._checkpoint_path = (
                Path(checkpoint_store_path)
                if checkpoint_store_path
                else (checkpoint_path_for(journal.path))
            )
            self._open_live_checkpoint(journal)
        self.enable_metrics = bool(enable_metrics)
        self.auth_timeout = max(safe_float(auth_timeout, default=DEFAULT_AUTH_TIMEOUT), 0.1)
        self.metrics_token = metrics_token or None
        self.metrics_query_token_ok = bool(metrics_query_token_ok)
        from synapse_channel.core.hub_handshake import normalise_allow_origins

        self.allowed_origins = normalise_allow_origins(tuple(allowed_origins or ()))
        self.advertised_host = (advertised_host or "").strip() or None
        self._bind_host = DEFAULT_HOST
        self._bind_port = DEFAULT_PORT
        self._bound_address: tuple[str, int] | None = None
        self._serving = asyncio.Event()
        self.insecure_off_loopback = bool(insecure_off_loopback)
        self.insecure_plaintext_at_rest = bool(insecure_plaintext_at_rest)
        self.rate_limiter = rate_limiter
        self.host_rate_limiter = host_rate_limiter
        self.durable_ingress_quota = durable_ingress_quota
        self.guard_evidence_quota = DurableIngressQuota(
            max_events=100,
            max_bytes=262_144,
            window_seconds=60.0,
        )
        self.authenticator = authenticator
        if isinstance(per_message_auth_keys, Mapping):
            self.per_message_auth_keys = dict(per_message_auth_keys)
        else:
            self.per_message_auth_keys = {key.key_id: key for key in (per_message_auth_keys or [])}
        self.require_per_message_auth = bool(require_per_message_auth)
        self.per_message_auth_replay_store = per_message_auth_replay_store
        self.per_message_auth_sequence_floor_mode = SequenceFloorMode(
            per_message_auth_sequence_floor_mode
        )
        self._message_replay = MessageReplayCache(
            window_seconds=safe_float(
                per_message_auth_window_seconds, default=DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS
            ),
            max_entries=safe_int(per_message_auth_replay_capacity, default=4096, min_value=1),
            durable=self.per_message_auth_replay_store,
            sequence_floor_mode=self.per_message_auth_sequence_floor_mode,
        )
        self.signed_event_trust_bundle = signed_event_trust_bundle
        self.capability_card_trust_bundle = capability_card_trust_bundle
        self.acl_policy = acl_policy
        self.require_acl = bool(require_acl)
        self.role_grants = role_grants
        self.require_role_claim = bool(require_role_claim)
        self.require_fencing_epoch = bool(require_fencing_epoch)
        if identity_enrollment_path and (identity_trust_bundle is None or journal is None):
            raise ValueError(
                "online identity enrolment needs an identity trust bundle and a durable "
                "journal: pass --identity-trust and --db with --identity-enrollments"
            )
        self.static_identity_trust = identity_trust_bundle
        self.identity_enrollment_path = (
            Path(identity_enrollment_path).expanduser() if identity_enrollment_path else None
        )
        self.enrolled_identity_keys = (
            load_enrolled_keys(self.identity_enrollment_path)
            if self.identity_enrollment_path is not None
            else {}
        )
        self.identity_trust_bundle = (
            merge_enrolled_keys(identity_trust_bundle, self.enrolled_identity_keys)
            if identity_trust_bundle is not None and self.identity_enrollment_path is not None
            else identity_trust_bundle
        )
        self.identity_enrollment_namespaces = frozenset(
            namespace.strip() for namespace in identity_enrollment_namespaces if namespace.strip()
        )
        self.enrollment_rate = EnrollmentRateLimiter(
            limit=max(0, int(identity_enrollment_rate)),
            window_seconds=max(0.0, float(identity_enrollment_window_seconds)),
        )
        self.require_identity_binding = bool(require_identity_binding)
        self.identity_pin_path = Path(identity_pin_path).expanduser() if identity_pin_path else None
        self.identity_pins = IdentityPinStore(path=self.identity_pin_path)
        self.private_directed_messages = bool(private_directed_messages)
        self.warn_stale_recipients = bool(warn_stale_recipients)
        self.recipient_liveness_window = max(
            safe_float(
                recipient_liveness_window,
                default=DEFAULT_RECIPIENT_LIVENESS_WINDOW,
            ),
            0.0,
        )
        self.waiter_liveness_window = max(
            safe_float(waiter_liveness_window, default=DEFAULT_WAITER_LIVENESS_WINDOW),
            0.0,
        )
        self._recipient_liveness = RecipientLiveness(window_seconds=self.recipient_liveness_window)
        if multihub_serving_policy is not None:
            check_identity_grants(
                multihub_serving_policy,
                identity_trust_bundle=self.identity_trust_bundle,
                require_identity_binding=self.require_identity_binding,
            )
        self.multihub_serving_policy = multihub_serving_policy
        self.spend_ledger = spend_ledger
        self.namespace_ownership = namespace_ownership
        self.claim_peers = dict(claim_peers) if claim_peers else None
        self.claim_forwarder = claim_forwarder
        self.relay_peers = dict(relay_peers) if relay_peers else None
        self.relay_forwarder = relay_forwarder
        self.message_peers = dict(message_peers) if message_peers else None
        self.message_forwarder = message_forwarder
        self.message_forward_ttl = max(
            1.0, safe_float(message_forward_ttl, default=DEFAULT_FORWARD_TTL_SECONDS)
        )
        self.message_forward_ledger = (
            journal.message_forward if journal is not None else MessageForwardLedger.in_memory()
        )
        self.require_relay_reason = bool(require_relay_reason)
        self.require_two_person_relay = bool(require_two_person_relay)
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
            system=self.system,
            send_json=self.send_json,
        )
        self.channels = ChannelRegistry()
        self.max_msg_bytes = safe_int(max_msg_bytes, default=DEFAULT_MAX_MSG_BYTES, min_value=1)
        self.clock = clock or time.monotonic
        self._started = self.clock()
        self.counters = HubCounters()
        if self.journal is not None:
            self.counters.operation_outbox_pending = self.journal.pending_operation_outbox_count()
        self.clients = HubClientRegistry(
            counters=self.counters,
            max_clients=max_clients,
            max_unauth_clients=max_unauth_clients,
            max_connections_per_host=max_connections_per_host,
            takeover_cooldown=takeover_cooldown,
            clock=self.clock,
            takeover_oscillation_window=takeover_oscillation_window,
            takeover_oscillation_threshold=takeover_oscillation_threshold,
            takeover_quarantine=takeover_quarantine,
            lease_offline_ttl=lease_offline_ttl,
        )
        self.max_clients = self.clients.max_clients
        self.max_unauth_clients = self.clients.max_unauth_clients
        self.max_connections_per_host = self.clients.max_connections_per_host
        self.takeover_cooldown = self.clients.takeover_cooldown
        self.takeover_oscillation_window = self.clients.takeover_oscillation_window
        self.takeover_oscillation_threshold = self.clients.takeover_oscillation_threshold
        self.takeover_quarantine = self.clients.takeover_quarantine
        self.lease_offline_ttl = self.clients.ownership.offline_ttl
        if self.multihub_serving_policy is not None:
            # A grant naming an identity key reads the registration this hub verified.
            self.multihub_serving_policy = dataclasses.replace(
                self.multihub_serving_policy, identity_source=self.clients.identity_proof
            )
        self.claim_holders = ClaimHolderPresence(
            clock=self.clock, started_at=self._started, window=self.lease_offline_ttl
        )
        self.shutdown_close_timeout = max(
            safe_float(shutdown_close_timeout, default=DEFAULT_SHUTDOWN_CLOSE_TIMEOUT), 0.1
        )
        self.max_history = safe_int(max_history, default=DEFAULT_MAX_HISTORY, min_value=1)
        self.max_findings_per_agent = safe_int(
            max_findings_per_agent, default=DEFAULT_MAX_FINDINGS_PER_AGENT, min_value=1
        )
        self.compact_hint_threshold = safe_int(
            compact_hint_threshold, default=DEFAULT_COMPACT_HINT_THRESHOLD, min_value=1
        )
        self.dead_letter_escalation_threshold = safe_int(
            dead_letter_escalation_threshold,
            default=DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD,
            min_value=0,
        )
        self.dead_letter_forwarder = dead_letter_forwarder
        self.board_task_cap = (
            safe_int(board_task_cap, default=1, min_value=1) if board_task_cap is not None else None
        )
        self.relay_log = Path(relay_log) if relay_log else None
        self.relay_max_lines = safe_int(
            relay_max_lines, default=DEFAULT_RELAY_MAX_LINES, min_value=1
        )
        self.dead_letters = DeadLetterLedger(max_age_seconds=DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS)
        self.pending_receipts = PendingReceipts()
        self.mailbox_pending = MailboxPendingTracker(self.journal)
        self._relay = RelayMirror(self.relay_log, self.relay_max_lines)
        self._broadcaster = HubBroadcaster(
            self.clients,
            self._relay,
            system=self.system,
            online_agents=self.online_agents,
        )
        self.hub_id = hub_id or f"syn-{uuid.uuid4().hex[:8]}"
        self.stable_delivery_hub_id = hub_id
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
            send_json=self.send_json,
            system=self.system,
        )
        self._identity_gate = HubIdentityGate(
            require_identity_binding=self.require_identity_binding,
            identity_trust_bundle=self.identity_trust_bundle,
            send_json=self.send_json,
            system=self.system,
            pin_store=self.identity_pins,
        )
        self.connected_clients = self.clients.connected_clients
        self.unauth_clients = self.clients.unauth_clients
        self.agent_sockets = self.clients.agent_sockets
        self.agent_roles = self.clients.agent_roles
        self.socket_agent = self.clients.socket_agent
        self.waits: dict[str, set[str]] = {}
        self.capabilities = CapabilityRegistry(trust_bundle=capability_card_trust_bundle)
        self._connection = HubConnection(
            self.clients,
            self.capabilities,
            authenticator=self.authenticator,
            auth_timeout=self.auth_timeout,
            rate_limiter=self.rate_limiter,
            handle_message=self.handle_message,
            send_json=self.send_json,
            system=self.system,
            online_agents=self.online_agents,
            broadcast_presence=self._broadcast_presence,
            drop_waits=self._drop_waits,
            forget_liveness=self._recipient_liveness.forget,
            abort_uploads=(self.attachment_store.abort_sender if self.attachment_store else None),
            agent_left=self._claim_holder_left,
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
            counters=self.counters,
            hub_id=self.hub_id,
            send_json=self.send_json,
            system=self.system,
        )
        self._relay_forwarding = OperatorRelayForwarding(
            namespace_ownership=self.namespace_ownership,
            relay_peers=self.relay_peers,
            relay_forwarder=self.relay_forwarder,
            observed_asserting_hubs=self.observed_asserting_hubs,
            hub_id=self.hub_id,
            journal=self.journal,
            send_json=self.send_json,
            system=self.system,
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
            protected_write_policies=protected_write_policies,
        )
        self.state = seeded.state
        self.relay_approvals = seeded.relay_approvals
        self.state_mutations = SerializedStateMutationActor()
        self.journal_corrupt_rows = seeded.corrupt_rows
        self._journal_recovery_gate = HubJournalRecoveryGate(
            self.journal_corrupt_rows,
            send_json=self.send_json,
            system=self.system,
        )
        # The liveness query view combines the reaction store with the live roster and
        # the last-seen map (built with ``state`` above), so it is wired here, after
        # ``state`` exists. The store itself is created earlier so the connection's
        # forget hook and the frame handler's touch can reference it.
        self.liveness = HubLivenessView(
            self._recipient_liveness,
            enabled=self.warn_stale_recipients,
            waiter_window_seconds=self.waiter_liveness_window,
            online_agents=self.online_agents,
            agent_sockets=self.agent_sockets,
            last_seen=self.state.last_seen,
            clock=self.clock,
        )
        self.chat_history = seeded.chat_history
        # K4-WF8: a retried chat (same sender and client_msg_id) whose first copy reached
        # a live recipient is answered with a duplicate notice instead of routed again.
        # The memory is per process: the journal does not record whether a copy was
        # received, and re-seeding from it would suppress a legitimate redelivery.
        self.chat_dedupe = ChatDedupe()
        self.pending_receipts.restore(seeded.pending_receipts)
        self.blackboard = seeded.blackboard
        self._dark_seats = DarkSeatMonitor(
            claims=lambda: self.state.claims,
            tasks=lambda: self.blackboard.tasks,
            has_live_waiter=self.liveness.has_live_waiter,
            broadcast=self.broadcast,
            system=self.system,
        )
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
    def message_seq(self) -> int:
        """Current per-hub message-id high-water mark (owned by the ledger guard)."""
        return self._ledger.message_seq

    def next_msg_id(self) -> int:
        """Return a strictly increasing per-hub message sequence number."""
        return self._ledger.next_msg_id()

    def remember(self, data: dict[str, Any], response: dict[str, Any]) -> None:
        """Cache the response of an applied mutation under its idempotency key.

        Handler surface: the ledger guard owns the cache; a handler that applied
        a mutation outside the atomic-operation path records its response here.
        """
        self._ledger.remember(data, response)

    def reserve_finding_slot(self, agent: str) -> tuple[bool, str]:
        """Reserve one durable-finding quota slot for ``agent`` (handler surface)."""
        return self._ledger.reserve_finding_slot(agent)

    @property
    def finding_quota(self) -> FindingQuota:
        """Return the copyable finding quota used by transactional memory writes."""
        return self._ledger.finding_quota

    async def _maybe_replay_duplicate(
        self, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Replay the cached response for a duplicate mutation, if any.

        Thin wrapper over :class:`HubLedgerGuard`, injecting the hub's per-socket
        send so the guard re-sends the original response to the duplicate's sender.
        """
        outcomes: list[str] = []
        replayed = await self._ledger.maybe_replay_duplicate(
            msg_type,
            data,
            websocket,
            self.send_json,
            outcomes.append,
        )
        for outcome in outcomes:
            self._record_atomic_outcome(outcome)
        if replayed and outcomes == ["replayed"]:
            await self.settle_atomic_operation(data)
        return replayed

    def _record_atomic_outcome(self, outcome: str) -> None:
        """Increment one bounded, label-free atomic-operation decision counter."""
        if outcome == "inserted":
            self.counters.atomic_operations_inserted += 1
            self.counters.operation_outbox_pending += 1
        elif outcome == "replayed":
            self.counters.atomic_operations_replayed += 1
        elif outcome == "conflict":
            self.counters.atomic_operations_conflicts += 1

    async def settle_atomic_operation(self, data: dict[str, Any]) -> None:
        """Mark a committed evidence intent projected after successful transport."""
        if self.journal is None:
            return
        operation_key = self._ledger.idempotency_key(data)
        if not operation_key:
            return
        stored = self.journal.get_operation(operation_key)
        if stored is None:
            return
        try:
            await asyncio.to_thread(
                self.journal.mark_operation_intent_delivered,
                operation_key,
                f"local:{stored.response_sha256}",
            )
            self.counters.operation_outbox_pending = max(
                0, self.counters.operation_outbox_pending - 1
            )
        except KeyError:
            return

    async def run_atomic_operation(
        self,
        data: dict[str, Any],
        mutate: Callable[[Any], Any],
        prepare: Callable[[Any], OperationDraft | None],
        *,
        subject: Any | None = None,
        publish_candidate: Callable[[Any], None] | None = None,
        persist_uncommitted: Callable[[Any], None] | None = None,
        publish: Callable[[Any], None] | None = None,
    ) -> AtomicExecution | None:
        """Run a keyed journal-backed mutation through the atomic operation actor."""
        if self.journal is None:
            return None
        operation_key = self._ledger.idempotency_key(data)
        if not operation_key:
            return None
        request_digest = canonical_request_digest(data)
        return await self._run_keyed_atomic_operation(
            operation_key,
            request_digest,
            mutate,
            prepare,
            conflict=lambda existing: idempotency_conflict_response(
                sender=str(data.get("sender") or ""), reference=existing.response
            ),
            subject=subject,
            publish_candidate=publish_candidate,
            persist_uncommitted=persist_uncommitted,
            publish=publish,
            allow_legacy_digestless_replay=True,
            require_committed_response=False,
        )

    async def run_authenticated_protected_write_operation(
        self,
        authenticated: AuthenticatedProtectedRequest,
        mutate: Callable[[Any], Any],
        prepare: Callable[[Any], OperationDraft],
        *,
        limits: ProtectedWriteProposalLimits,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        current_principal: Callable[[], str],
        clock: Callable[[], float],
        conflict: Callable[[OperationRecord], dict[str, Any]],
    ) -> AtomicExecution:
        """Recheck ingress authority inside the durable mutation actor.

        Parameters
        ----------
        authenticated:
            Server-retained ingress result, never a deserialized client object.
        mutate:
            Existing synchronous admission/transition mutation on private state.
        prepare:
            Existing durable response/event builder.
        limits:
            Explicit enrolled wire limits.
        current_enrollments:
            Current protected registry, ordered with mutations by this actor.
        current_principal:
            Current authenticated transport principal, not a claimed sender.
        clock:
            Fresh trusted server clock.
        conflict:
            Existing protected conflict response builder.

        Returns
        -------
        AtomicExecution
            Existing journal-backed outcome, not writer execution or settlement.

        Notes
        -----
        Authentication must already have consumed the wire replay nonce.
        All current-context callbacks are synchronous, trusted and I/O-free.
        Read-only verbs retain a separate admission path. This method does not
        install dispatch handlers or prove OS credential/namespace isolation.
        """

        def authorize() -> None:
            recheck_authenticated_protected_request(
                authenticated,
                enrollments=current_enrollments(),
                authenticated_principal=current_principal(),
                now=clock(),
            )

        enrollment = authenticated.enrollment
        return await self._run_protected_write_operation(
            authenticated.parsed.canonical_bytes,
            mutate,
            prepare,
            limits=limits,
            authenticated_principal=enrollment.principal,
            authority_id=enrollment.authority_id,
            authority_continuity=enrollment.authority_continuity,
            conflict=conflict,
            authorize=authorize,
        )

    async def _run_protected_write_operation(
        self,
        raw: str | bytes,
        mutate: Callable[[Any], Any],
        prepare: Callable[[Any], OperationDraft],
        *,
        limits: ProtectedWriteProposalLimits,
        authenticated_principal: str,
        authority_id: str,
        authority_continuity: str,
        conflict: Callable[[OperationRecord], dict[str, Any]],
        authorize: Callable[[], None] | None = None,
    ) -> AtomicExecution:
        """Commit an already-authorized protected mutation through the sole actor.

        This internal boundary is not a wire handler. The caller must authenticate
        and enforce current session, replay, enrollment and operation policy before
        entry. Context parameters must come from server authority, never raw input.
        The mutation callback must recheck current state witnesses under the actor
        lock; admission policy checked before waiting for that lock may be stale.
        Read-only prepare/status do not use this mutation boundary.
        """
        operation_key = protected_write_operation_key(
            raw,
            limits=limits,
            authenticated_principal=authenticated_principal,
            authority_id=authority_id,
            authority_continuity=authority_continuity,
        )
        parsed = parse_protected_write_request(raw, limits=limits)
        if json.loads(parsed.canonical_bytes)["type"] in (
            "protected_write_prepare",
            "protected_write_status",
        ):
            raise ValueError("read-only protected requests cannot use the mutation boundary")
        return await self._run_keyed_atomic_operation(
            operation_key,
            parsed.request_digest,
            mutate,
            prepare,
            conflict=conflict,
            allow_legacy_digestless_replay=False,
            require_committed_response=True,
            authorize=authorize,
        )

    async def _run_keyed_atomic_operation(
        self,
        operation_key: str,
        request_digest: str,
        mutate: Callable[[Any], Any],
        prepare: Callable[[Any], OperationDraft | None],
        *,
        conflict: Callable[[OperationRecord], dict[str, Any]],
        allow_legacy_digestless_replay: bool,
        require_committed_response: bool,
        authorize: Callable[[], None] | None = None,
        subject: Any | None = None,
        publish_candidate: Callable[[Any], None] | None = None,
        persist_uncommitted: Callable[[Any], None] | None = None,
        publish: Callable[[Any], None] | None = None,
    ) -> AtomicExecution:
        """Share one journal/cache/actor for legacy and protected operation keys."""
        if self.journal is None:
            raise ValueError("atomic execution requires a durable journal")
        journal = self.journal

        def commit(draft: OperationDraft) -> Any:
            return journal.commit_operation(
                operation_key=operation_key,
                request_digest=request_digest,
                response=draft.response,
                events=draft.events,
                intent=draft.intent,
                response_event_seq_field=draft.response_event_seq_field,
                finalize_response=draft.finalize_response,
            )

        mutation_subject = self.state if subject is None else subject
        candidate_publisher: Callable[[Any], None]
        if subject is None:
            candidate_publisher = self.state.publish_from
        elif publish_candidate is not None:
            candidate_publisher = publish_candidate
        else:
            raise ValueError("a non-state atomic subject requires a candidate publisher")

        execution = await self.state_mutations.run_atomic(
            mutation_subject,
            mutate,
            request_digest=request_digest,
            lookup=lambda: self._ledger.lookup_operation(operation_key),
            prepare=prepare,
            commit=commit,
            remember=self._ledger.remember_operation,
            conflict=conflict,
            publish_candidate=candidate_publisher,
            persist_uncommitted=persist_uncommitted,
            publish=publish,
            allow_legacy_digestless_replay=allow_legacy_digestless_replay,
            require_committed_response=require_committed_response,
            authorize=authorize,
        )
        self._record_atomic_outcome(execution.outcome)
        return execution

    def system(self, payload: str, **extra: Any) -> dict[str, Any]:
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

    def permitted_role_claims(self, name: str, roles: tuple[str, ...]) -> tuple[str, ...]:
        """Return the subset of declared ``roles`` ``name`` is permitted to bind.

        With role-claim enforcement off — the default open/loopback posture — every
        declared role is permitted, so a single-user dev hub binds roles exactly as
        before. With ``--require-role-claim`` on, a role is kept when either:

        - the role-grant store (``synapse role`` / ``--role-grants``) authorises
          ``name`` for it, or
        - the loaded ACL policy grants ``role-claim`` on target kind ``role`` for
          that role value (namespace-scoped like every other ACL rule).

        An unauthorised role is dropped and logged as a squatting attempt rather
        than dropping the socket. Enforcement with no store and no matching ACL
        rule denies the claim (fail closed). The gate keys off the self-reported
        ``name``, so pair it with a connect token and identity binding to be a real
        boundary.
        """
        if not self.require_role_claim:
            return roles
        grants = self.role_grants or RoleGrants({})
        store_permitted = set(grants.authorised_roles(name, roles))
        permitted: list[str] = []
        for role in roles:
            if role in store_permitted or self._acl_allows_role_claim(name, role):
                permitted.append(role)
        denied = tuple(role for role in roles if role not in permitted)
        if denied:
            logger.warning("role-claim denied for %s: %s", name, ", ".join(denied))
        return tuple(permitted)

    def _acl_allows_role_claim(self, name: str, role: str) -> bool:
        """Return whether the ACL policy grants ``name`` the ``role-claim`` on ``role``."""
        policy = self.acl_policy
        if policy is None:
            return False
        decision = evaluate_access(
            subject=name,
            project=project_of(name),
            permission=ROLE_CLAIM,
            target=Target("role", role),
            policy=policy,
        )
        return decision.decision == WOULD_ALLOW

    def roles_of(self, name: str) -> tuple[str, ...]:
        """Return the roles ``name`` currently answers to (empty tuple if none)."""
        return self.clients.roles_of(name)

    def set_wake_capability(self, name: str, capability: str) -> None:
        """Bind the receiver wake capability declared on an identity's registration."""
        self.clients.set_wake_capability(name, capability)

    def wake_capability_of(self, name: str) -> str:
        """Return the declared receiver wake capability for ``name``."""
        return self.clients.wake_capability_of(name)

    def observing_identities(self, target: str) -> tuple[str, ...]:
        """Return connected identities the ACL policy grants ``observe`` on ``target``.

        Under directed-message routing an observer (a live monitor or auditor) still
        receives a directed message it is not a party to only when it holds an
        ``observe`` grant. With no ACL policy configured there are no observers, so
        directed routing narrows to the recipients alone; the grant is scoped to the
        observer's own namespace, so an operator designates observers without opening
        the traffic to everyone.
        """
        policy = self.acl_policy
        if policy is None:
            return ()
        return tuple(
            name
            for name in self.online_agents()
            if evaluate_access(
                subject=name,
                project=project_of(name),
                permission=OBSERVE,
                target=Target("agent", target),
                policy=policy,
            ).decision
            == WOULD_ALLOW
        )

    def recipients_without_live_waiter(self, recipients: Iterable[str]) -> tuple[str, ...]:
        """Present recipients with no proof of liveness — the ones to warn about.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_liveness.HubLivenessView.recipients_without_live_waiter`,
        kept because the chat handler and tests call ``hub.recipients_without_live_waiter``.
        """
        return self.liveness.recipients_without_live_waiter(recipients)

    def roster_liveness(self) -> dict[str, dict[str, Any]]:
        """Per-agent liveness annotation for the ``/who`` roster (handler surface).

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_liveness.HubLivenessView.roster_liveness`, kept
        because the who-snapshot handler and tests call ``hub.roster_liveness``.
        """
        return self.liveness.roster_liveness()

    def _claim_holder_left(self, name: str) -> None:
        """Start the offline window for ``name`` when it still holds a claim."""
        if any(claim.owner == name for claim in self.state.claims.values()):
            self.claim_holders.left(name)

    def uptime_seconds(self) -> float:
        """Return seconds elapsed since the hub was constructed."""
        return max(0.0, self.clock() - self._started)

    async def send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        """Serialise and send one message to a single socket (handler surface)."""
        await self._broadcaster.send_json(websocket, data)

    async def mirror_to_relay(self, data: dict[str, Any]) -> None:
        """Mirror one broadcast to the lite relay log via :class:`RelayMirror`.

        Handler surface: the chat handler mirrors a channel-scoped message it
        fans out itself; the append, lite encoding, and bounded trimming live in
        :class:`~synapse_channel.core.hub_relay.RelayMirror`.
        """
        await self._relay.mirror_async(data)

    async def broadcast(self, data: dict[str, Any]) -> frozenset[str]:
        """Fan out with bounded writes, returning successful bound socket names."""
        return await self._broadcaster.broadcast(data)

    async def broadcast_directed(
        self, data: dict[str, Any], *, names: Iterable[str], sender_socket: Any
    ) -> frozenset[str]:
        """Return successful writes to recipients and granted observers only."""
        return await self._broadcaster.send_directed(data, names=names, sender_socket=sender_socket)

    async def _broadcast_presence(self, event: str, agent: str | None = None) -> None:
        """Broadcast a presence update naming who joined or left."""
        await self._broadcaster.broadcast_presence(event, agent)

    async def send_to_agent(self, agent: str, data: dict[str, Any]) -> bool:
        """Send to a named agent's socket; return whether the send succeeded."""
        return await self._broadcaster.send_to_agent(agent, data)

    def _drop_waits(self, agent: str) -> None:
        """Remove a disconnecting agent's outgoing wait edges.

        Edges key waited tasks, not incumbent holders, so nothing points *at*
        the agent; a holder going offline is covered by lease expiry plus the
        live ownership resolution at cycle-check time.
        """
        self.waits.pop(agent, None)

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

    def _guard_exposure(self, host: str, *, tls_active: bool = False) -> None:
        """Refuse — or, when overridden, warn — before binding an exposed host.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_ingress.HubIngress.guard_exposure`, kept
        because :meth:`serve` and tests call ``hub._guard_exposure`` directly.
        ``tls_active`` states whether the bind terminates TLS; without it a token
        off loopback is refused as a plaintext-transport exposure (downgradable
        with ``--insecure-off-loopback``).
        """
        self._ingress.guard_exposure(host, tls_active=tls_active)

    def _guard_at_rest(self, host: str) -> None:
        """Refuse — or, when overridden, warn — before binding with a plaintext store.

        Proportionate to exposure: off loopback a plaintext ``--db`` event store is
        refused (the durable log would sit unencrypted on a networked host's disk)
        unless it is encrypted or ``--insecure-plaintext-at-rest`` is set. A loopback
        bind or a hub with no durable journal is unaffected.
        """
        journal = self.journal
        guard_at_rest(
            host,
            db=journal.path if journal is not None else None,
            encrypted=journal is None or journal.encrypted,
            insecure_plaintext_at_rest=self.insecure_plaintext_at_rest,
            sqlcipher_available=sqlcipher_available(),
            logger=logger,
        )

    async def _resolve_sender(
        self,
        sender: str,
        websocket: Any,
        *,
        takeover: bool = False,
        lease_requested: bool = False,
        owner_lease: str = "",
    ) -> str | None:
        """Bind a socket to a sender name, enforcing ownership and uniqueness.

        Thin wrapper over
        :meth:`~synapse_channel.core.hub_ingress.HubIngress.resolve_sender`, kept
        because :meth:`handle_message` calls ``self._resolve_sender`` directly.
        """
        return await self._ingress.resolve_sender(
            sender,
            websocket,
            takeover=takeover,
            lease_requested=lease_requested,
            owner_lease=owner_lease,
        )

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
            await self.send_json(
                websocket, self.system("Malformed JSON.", msg_type=MessageType.ERROR)
            )
            return

        # A valid-JSON frame need not be an object: ``["x"]``, ``null``, ``42`` all
        # decode cleanly, then ``data.get("sender")`` below would raise
        # AttributeError and — caught nowhere on the per-connection loop — drop the
        # socket with a 1011. Reject a non-object envelope at the boundary instead.
        if not isinstance(data, dict):
            await self.send_json(
                websocket,
                self.system("Malformed frame: expected a JSON object.", msg_type=MessageType.ERROR),
            )
            return

        # Charge every frame — heartbeats included — to its remote host before any
        # further work, so one host cannot flood the hub regardless of agent name.
        if self.host_rate_limiter is not None and not self.host_rate_limiter.allow(
            self._remote_host(websocket)
        ):
            await self.send_json(
                websocket, self.system("Host rate limit exceeded.", msg_type=MessageType.ERROR)
            )
            return

        # A routing/identity field present but not a string (``sender: [..]``, ``type: true``)
        # would otherwise be ``str()``-coerced into a plausible identity or route below. Refuse
        # it *after* the per-host charge above — so a flood of malformed frames is still
        # rate-limited, not merely cheaply rejected — and before coercion, so a type-confused
        # envelope never binds a name or addresses a target it does not spell out.
        mistyped = HubIngress.mistyped_text_field(data)
        if mistyped is not None:
            await self.send_json(
                websocket,
                self.system(
                    f"Malformed frame: {mistyped!r} must be a string.",
                    msg_type=MessageType.ERROR,
                ),
            )
            return

        sender = str(data.get("sender") or "").strip() or f"anon-{id(websocket)}"
        target = str(data.get("target") or "all")
        msg_type = str(data.get("type") or MessageType.CHAT).strip().lower()
        payload = str(data.get("payload") or "")

        # Hub/protocol identities are provenance markers, never agent names. Refuse
        # them before authentication or trust-on-first-use identity verification so
        # a signed hostile registration cannot leave a durable pin behind for a name
        # that no client is ever allowed to own. The registry repeats the predicate
        # at its binding boundary so direct callers cannot bypass this early guard.
        if self.clients.is_reserved_sender(sender):
            await self._resolve_sender(sender, websocket)
            return

        # Capture whether this socket was already bound before authorising, so a
        # secured hub can send the withheld welcome the moment it first authenticates.
        was_bound = self.clients.is_bound(websocket)
        if not await self._authorise(sender, data, websocket):
            return

        # On the first (name-binding) frame, resolve the connection credential to the
        # claimed identity before the name is trusted, so a -rx mailbox or role claim
        # rests on a proven identity. A socket that cannot prove it is refused and closed.
        # A peer hub proves its name with the pinned mutual-TLS certificate its serving
        # grant is bound to, not with a registration signature.
        if not was_bound and not self._peer_hub_identity_proven(sender, websocket):
            if not await self._identity_gate.verify_identity(sender, data, websocket):
                return
            self._record_identity_proof(sender, data, websocket)

        # ``token`` is a connection credential, never application data. Keep it
        # through first-use identity verification because the registration
        # signature covers the complete frame, then consume it before any name
        # resolution, routing, relay, history, or journal path can observe it.
        data.pop("token", None)

        resolved = await self._resolve_sender(
            sender,
            websocket,
            takeover=bool(data.get("takeover")),
            lease_requested=bool(data.get("lease")),
            owner_lease=str(data.get("owner_lease") or ""),
        )
        if resolved is None:
            return
        sender = resolved
        if self.authenticator is not None and not was_bound:
            await self._send_welcome(websocket)

        def touch_state(state: Any) -> bool:
            # The resolver may have awaited while closing a previous owner. A
            # later takeover can detach this websocket before its serialized
            # heartbeat reaches the state actor; a superseded socket must not
            # refresh presence or trigger lease-expiry publication.
            if self.clients.bound_agent(websocket) != sender:
                return False
            state.heartbeat(sender)
            return True

        def publish_heartbeat(applied: bool) -> None:
            if not applied:
                return
            # A heartbeat can expire leases; a wait on a task that just lost
            # its holder is stale and must not refuse a later legitimate wait.
            self.waits = prune_waits(self.waits, self.state.claims)

        heartbeat_applied = await self.state_mutations.run(
            self.state,
            touch_state,
            publish=publish_heartbeat,
        )
        # Sender resolution can suspend while it closes a superseded owner. A
        # second takeover may win during that close and detach this socket before
        # the first handler resumes here. Never let the stale continuation put
        # itself back into agent_sockets after socket_agent has moved to the real
        # winner; both maps must remain one bijection.
        if not heartbeat_applied or self.clients.bound_agent(websocket) != sender:
            logger.info(
                "superseded sender continuation dropped sender=%s remote_host=%s",
                sender,
                self.clients.remote_host(websocket),
            )
            return
        is_new_agent = self.clients.set_agent_socket(sender, websocket)
        if not was_bound:
            self.claim_holders.returned(sender)
            self.clients.bind_protocol_version(
                sender, websocket, read_protocol_version(data.get("protocol_version"))
            )
        try:
            delivery_session = bind_delivery_registration(
                self.clients,
                sender=sender,
                websocket=websocket,
                data=data,
                msg_type=msg_type,
                was_bound=was_bound,
                durable=self.journal is not None,
                stable_hub_id=self.stable_delivery_hub_id,
            )
        except DeliveryRefusal as exc:
            await self.send_json(
                websocket,
                self.system(
                    str(exc),
                    msg_type=MessageType.ERROR,
                    target=sender,
                    reason_code=exc.code,
                ),
            )
            await self._close_socket(websocket, code=4020, reason="delivery registration refused")
            return
        if delivery_session is not None:
            from synapse_channel.core.handlers.delivery_modes import (
                supersede_old_delivery_sessions,
            )

            await supersede_old_delivery_sessions(
                self, target=sender, incarnation=delivery_session.incarnation
            )
            await self.send_json(
                websocket,
                self.system(
                    "Delivery session registered.",
                    msg_type=MessageType.DELIVERY_SESSION,
                    target=sender,
                    incarnation=delivery_session.incarnation,
                    capabilities=delivery_session.capabilities,
                    protocol_version=3,
                ),
            )
        if not was_bound and self.journal is not None:
            # Receipt notifications are a durable at-least-once outbox. A sender
            # that was offline for an ACK or a prior transport failure receives
            # the same stable notification ids when it next proves this identity.
            from synapse_channel.core.handlers.delivery_feedback import (
                deliver_pending_receipt_notifications,
            )

            await deliver_pending_receipt_notifications(self, sender=sender, websocket=websocket)
            from synapse_channel.core.handlers.delivery_modes import (
                deliver_pending_delivery_notifications,
            )

            await deliver_pending_delivery_notifications(self, sender=sender, websocket=websocket)
        if not was_bound:
            # A forwarded chat that settled while its sender was offline is reported now.
            from synapse_channel.core.message_forward_origin import (
                deliver_pending_forward_receipts,
            )

            await deliver_pending_forward_receipts(self, sender=sender)
        if not was_bound or msg_type != MessageType.HEARTBEAT:
            self.dead_letters.clear(sender)
        if self.warn_stale_recipients and (not was_bound or msg_type != MessageType.HEARTBEAT):
            # Seed the grace window on registration, then refresh on every genuine
            # reaction — any non-heartbeat frame — so directed delivery can classify
            # a recipient that is present but has gone deaf. A keepalive
            # heartbeat is deliberately not a reaction: it proves the socket, not the
            # agent. Only written when the warning is enabled, so the default open hub
            # keeps no per-frame liveness state.
            self._recipient_liveness.touch(sender, self.clock())
        if is_new_agent:
            await self._broadcast_presence("joined", sender)
        # A channel-scoped frame is audience-restricted, so its body must not land
        # in the hub log either — log the channel id and length, never the content.
        channel_id = str(data.get("channel") or "").strip()
        logged_payload = (
            f"<channel {terminal_text(channel_id)!r} body redacted, {len(payload)} chars>"
            if channel_id
            else (
                "<attachment body redacted>"
                if msg_type.startswith("attachment_")
                else terminal_text(self._redact_payload(payload))
            )
        )
        # Every field here crosses the untrusted wire boundary: a client controls
        # its own sender/target/type/channel and the payload. Render each one-line
        # with controls escaped so a crafted newline cannot forge a second log line
        # and a carriage return or ANSI cannot rewrite the operator's terminal.
        logger.info(
            "[%s -> %s] (%s): %s",
            terminal_text(sender),
            terminal_text(target),
            terminal_text(msg_type),
            logged_payload,
        )

        if (
            msg_type != MessageType.HEARTBEAT
            and self.rate_limiter is not None
            and not self.rate_limiter.allow(sender)
        ):
            self.counters.rate_limited += 1
            await self.send_json(
                websocket,
                self.system("Rate limit exceeded.", msg_type=MessageType.ERROR, target=sender),
            )
            return

        if not await self._verify_per_message_auth(sender, msg_type, data, websocket):
            self.counters.auth_failures += 1
            return

        if await self._journal_recovery_gate.refuse_mutation(sender, msg_type, websocket):
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
        data = dict(data)
        data["sender"] = sender
        data["type"] = msg_type
        if await self._maybe_replay_duplicate(msg_type, data, websocket):
            return
        handler = DISPATCH.get(msg_type)
        if handler is None:
            await self.send_to_agent(
                sender,
                self.system(
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
        the entry point :meth:`serve` hands to the modern asyncio server API.
        ``websockets`` 13.0 invokes this callback after ``process_request`` has
        already returned a non-upgrade HTTP response; later releases skip it.
        Ignore that closed probe connection instead of registering it as an agent.
        """
        response = getattr(websocket, "response", None)
        if response is not None and response.status_code != 101:
            return
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
        """``websockets`` request hook: handshake Origin/Host guard, then metrics/health HTTP.

        Always installed so browser Origin/Host enforcement runs even when metrics
        are disabled. Every path, the ``/metrics`` and ``/health`` probes included,
        must pass the handshake boundary first: a DNS-rebinding page names its own
        host, so an open loopback probe would otherwise answer it. Scrapers that
        reach the hub by another name are admitted through ``advertised_host``.
        The probes then delegate to
        :func:`~synapse_channel.core.hub_http.http_endpoint_response` (only when
        :attr:`enable_metrics` is set); other paths proceed to the WebSocket upgrade.
        """
        from synapse_channel.core.hub_handshake import (
            handshake_guard_response,
            http_forbidden,
            trusted_host_authorities,
        )

        authorities = trusted_host_authorities(
            bind_host=self._bind_host,
            bind_port=self._bind_port,
            advertised_host=self.advertised_host,
        )
        refusal = handshake_guard_response(
            request,
            allowed_origins=self.allowed_origins,
            trusted_authorities=authorities,
        )
        if refusal is not None:
            return refusal
        route = request.path.split("?", 1)[0]
        if route in ("/metrics", "/health"):
            if self.enable_metrics:
                return http_endpoint_response(self, request)
            # Metrics off: do not upgrade probe paths to WebSocket either.
            return http_forbidden("metrics disabled")
        return None

    @property
    def bound_address(self) -> tuple[str, int] | None:
        """Return the active TCP bind address, including an assigned port.

        The value is ``None`` before :meth:`serve` has bound its socket and
        after that server stops. In particular, callers that request port
        ``0`` receive the kernel-assigned port instead of having to perform a
        racy reserve-and-release probe.
        """
        return self._bound_address

    async def wait_until_serving(self, timeout: float = 3.0) -> tuple[str, int]:
        """Wait for :meth:`serve` to bind and return its actual TCP address.

        Parameters
        ----------
        timeout : float, optional
            Maximum seconds to wait for the socket bind.

        Returns
        -------
        tuple[str, int]
            Host and kernel-assigned port of the active listening socket.

        Raises
        ------
        TimeoutError
            If the hub does not bind before ``timeout``.
        RuntimeError
            If readiness is signalled without an active address.
        """
        await asyncio.wait_for(self._serving.wait(), timeout=timeout)
        if self._bound_address is None:
            raise RuntimeError("hub signalled readiness without a bound address")
        return self._bound_address

    @property
    def _checkpoint_store(self) -> MerkleCheckpointStore | None:
        """The open checkpoint chain, or ``None`` when anchoring is off or closed."""
        return None if self._live_checkpoint is None else self._live_checkpoint.store

    def _record_identity_proof(self, sender: str, data: dict[str, Any], websocket: Any) -> None:
        """Record the key a registration was verified under against the operator bundle.

        Only the operator-bundle posture proves a key an operator enrolled; the
        trust-on-first-use posture admits a key the client chose, so it records nothing.
        A serving grant naming ``identity_key_id`` reads this record.
        """
        signature = data.get("signature")
        key_id = signature.get("key_id") if isinstance(signature, dict) else None
        if self.require_identity_binding and isinstance(key_id, str) and key_id:
            self.clients.record_identity_proof(websocket, sender, key_id)

    def replace_enrolled_identity_keys(self, enrolled: dict[str, EventSignatureKey]) -> None:
        """Make ``enrolled`` the hub's online-enrolled identity keys (handler surface).

        The single place where an enrolment, rotation or revocation takes effect
        in memory. Three things must agree afterwards: the enrolled keys, the
        effective trust bundle (the operator's static bundle merged with them),
        and the bundle the identity gate verifies later registrations against.
        The caller has already written the audit record and persisted the store.

        Parameters
        ----------
        enrolled : dict[str, EventSignatureKey]
            Every enrolled key by key id, revoked ones included.

        Raises
        ------
        ValueError
            When the hub has no static identity trust bundle to merge with.
        IdentityEnrollmentError
            When the merge is refused; nothing has changed in that case.
        """
        static = self.static_identity_trust
        if static is None:
            raise ValueError("identity enrolment needs an identity trust bundle on the hub")
        bundle = merge_enrolled_keys(static, enrolled)
        self.enrolled_identity_keys = enrolled
        self.identity_trust_bundle = bundle
        self._identity_gate.replace_trust_bundle(bundle)

    def _peer_hub_identity_proven(self, sender: str, websocket: Any) -> bool:
        """Return whether ``sender`` is a peer hub proven by its pinned client certificate.

        The multi-hub serving policy keys each grant by the peer's registered id and
        admits it only over a live mutual-TLS connection whose certificate matches that
        grant's trust bundle. That is a proof of the name as strong as a registration
        signature, so an identity-bound hub accepts it for the peer's own id; every
        other name still has to present an enrolled signature.
        """
        policy = self.multihub_serving_policy
        return policy is not None and policy.authorise(sender=sender, websocket=websocket).allowed

    def _open_live_checkpoint(self, journal: EventStore) -> None:
        """Open the checkpoint chain, verify the log against it, then anchor it.

        Fail closed BEFORE serving: a truncated or rewritten log is a hard error
        at startup, never a quiet restart. Only then is the current state anchored
        as the newest chain link. A refused start owns no hub, so it releases the
        checkpoint connection it opened.
        """
        assert self._checkpoint_path is not None
        store = MerkleCheckpointStore(self._checkpoint_path)
        try:
            store.verify(journal)
            self._live_checkpoint = LiveCheckpoint(store, journal)
        except BaseException:
            store.close()
            raise

    async def _checkpoint_anchor_loop(self, live: LiveCheckpoint) -> None:
        """Anchor the live log every ``checkpoint_interval`` seconds while serving.

        The loop is cancelled before :meth:`_close_live_checkpoint` releases ``live``.
        """
        while True:
            await asyncio.sleep(self.checkpoint_interval)
            live.anchor()

    def _close_live_checkpoint(self) -> None:
        """Anchor the final state and release the checkpoint store (clean shutdown)."""
        live = self._live_checkpoint
        if live is None:
            return
        self._live_checkpoint = None
        try:
            live.anchor()
        finally:
            live.close()

    async def serve(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        """Run the hub's WebSocket server until cancelled.

        Always installs :meth:`_process_request` so Origin/Host handshake policy
        applies to every upgrade. With :attr:`enable_metrics` set, the same port
        also answers HTTP ``GET /metrics`` and ``GET /health``.

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
        self._guard_exposure(host, tls_active=ssl_context is not None)
        self._guard_at_rest(host)
        if self.journal is not None and self.stable_delivery_hub_id:
            self.journal.delivery.verify_origin_hub(self.hub_id)
        if (
            self._checkpoint_path is not None
            and self._live_checkpoint is None
            and isinstance(self.journal, EventStore)
        ):
            # A hub served again after a clean shutdown re-verifies before serving.
            self._open_live_checkpoint(self.journal)
        self._bind_host = host
        self._bind_port = int(port)
        self._bound_address = None
        self._serving.clear()
        stop = asyncio.Event()
        self._install_signal_handlers(asyncio.get_running_loop(), stop)
        started = False
        delivery_sweeper: asyncio.Task[None] | None = None
        forward_retrier: asyncio.Task[None] | None = None
        anchorer: asyncio.Task[None] | None = None
        try:
            async with (
                self._dark_seats.running(),
                serve(
                    self.handler,
                    host,
                    port,
                    max_size=self.max_msg_bytes,
                    max_queue=DEFAULT_MAX_QUEUE,
                    ping_interval=DEFAULT_PING_INTERVAL,
                    ping_timeout=DEFAULT_PING_TIMEOUT,
                    close_timeout=self.shutdown_close_timeout,
                    process_request=self._process_request,
                    ssl=ssl_context,
                    logger=ws_server_logger,
                ) as server,
            ):
                sockets = server.sockets
                if not sockets:
                    raise RuntimeError("hub server bound without a listening socket")
                socket_name = sockets[0].getsockname()
                self._bind_port = int(socket_name[1])
                self._bound_address = (host, self._bind_port)
                started = True
                self._serving.set()
                scheme = "wss" if ssl_context is not None else "ws"
                logger.info(
                    "Synapse Hub running on %s://%s:%d",
                    scheme,
                    host,
                    self._bind_port,
                )
                if self.journal is not None and self.stable_delivery_hub_id:
                    from synapse_channel.core.handlers.delivery_modes import (
                        delivery_expiry_loop,
                    )

                    delivery_sweeper = asyncio.create_task(delivery_expiry_loop(self))
                    delivery_sweeper.add_done_callback(lambda _task: stop.set())
                if self.message_peers:
                    from synapse_channel.core.message_forward_origin import (
                        message_forward_retry_loop,
                    )

                    forward_retrier = asyncio.create_task(message_forward_retry_loop(self))
                    forward_retrier.add_done_callback(lambda _task: stop.set())
                if self._live_checkpoint is not None:
                    anchorer = asyncio.create_task(
                        self._checkpoint_anchor_loop(self._live_checkpoint)
                    )
                    anchorer.add_done_callback(lambda _task: stop.set())
                await stop.wait()
                for background in (delivery_sweeper, forward_retrier, anchorer):
                    if background is not None and background.done():
                        background.result()
        except BaseException:
            if not started:
                # Unblock a startup waiter so it reports the failed bind
                # immediately instead of disguising it as a readiness timeout.
                self._serving.set()
            raise
        finally:
            for background in (delivery_sweeper, forward_retrier, anchorer):
                if background is not None:
                    background.cancel()
                    await asyncio.gather(background, return_exceptions=True)
            if started:
                # K4-N1: a clean stop anchors everything written, then releases
                # the checkpoint store; only a crash leaves an unanchored window.
                self._close_live_checkpoint()
            self._bound_address = None
            if started:
                self._serving.clear()
