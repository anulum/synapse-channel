# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Original 8370a8bf keyword defaults, frozen independently of the new records."""

from __future__ import annotations

from synapse_channel.core.agent_liveness import (
    DEFAULT_RECIPIENT_LIVENESS_WINDOW,
    DEFAULT_WAITER_LIVENESS_WINDOW,
    DEFAULT_WARN_STALE_RECIPIENTS,
)
from synapse_channel.core.dead_letter_escalation import DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.hub_defaults import (
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_COMPACT_HINT_THRESHOLD,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_CONNECTIONS_PER_HOST,
    DEFAULT_MAX_FINDINGS_PER_AGENT,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_MSG_BYTES,
    DEFAULT_RELAY_MAX_LINES,
    DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
    DEFAULT_TAKEOVER_COOLDOWN,
    DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD,
    DEFAULT_TAKEOVER_OSCILLATION_WINDOW,
    DEFAULT_TAKEOVER_QUARANTINE,
)
from synapse_channel.core.identity_enrollments import (
    DEFAULT_ENROLLMENT_RATE,
    DEFAULT_ENROLLMENT_WINDOW_SECONDS,
)
from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    DEFAULT_MAX_PROGRESS_PER_TASK,
)
from synapse_channel.core.merkle_checkpoint import DEFAULT_CHECKPOINT_INTERVAL
from synapse_channel.core.message_auth import DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS
from synapse_channel.core.message_auth_durable import SequenceFloorMode
from synapse_channel.core.message_forward_origin import DEFAULT_FORWARD_TTL_SECONDS
from synapse_channel.core.message_forward_transport import forward_message
from synapse_channel.core.multihub_claim_transport import forward_claim
from synapse_channel.core.multihub_serving import live_peer_certificate_der
from synapse_channel.core.name_ownership import DEFAULT_LEASE_OFFLINE_TTL
from synapse_channel.core.operator_relay_transport import relay_operator_action
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import MAX_CLAIMS_PER_AGENT, MAX_OFFERS_PER_AGENT

LEGACY_DEFAULTS: dict[str, object] = {
    "default_ttl_seconds": 3600.0,
    "hub_id": None,
    "journal": None,
    "attachment_store": None,
    "attachment_serving_policy": None,
    "rate_limiter": None,
    "host_rate_limiter": None,
    "durable_ingress_quota": None,
    "max_history": DEFAULT_MAX_HISTORY,
    "relay_log": None,
    "relay_max_lines": DEFAULT_RELAY_MAX_LINES,
    "max_progress": DEFAULT_MAX_PROGRESS,
    "max_progress_per_author": DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    "max_progress_per_task": DEFAULT_MAX_PROGRESS_PER_TASK,
    "board_task_cap": None,
    "max_findings_per_agent": DEFAULT_MAX_FINDINGS_PER_AGENT,
    "compact_hint_threshold": DEFAULT_COMPACT_HINT_THRESHOLD,
    "dead_letter_escalation_threshold": DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD,
    "dead_letter_forwarder": forward_dead_letter,
    "authenticator": None,
    "max_clients": DEFAULT_MAX_CLIENTS,
    "max_unauth_clients": None,
    "max_connections_per_host": DEFAULT_MAX_CONNECTIONS_PER_HOST,
    "max_msg_bytes": DEFAULT_MAX_MSG_BYTES,
    "max_claims_per_agent": MAX_CLAIMS_PER_AGENT,
    "max_offers_per_agent": MAX_OFFERS_PER_AGENT,
    "max_paths_per_claim": MAX_DECLARED_PATHS,
    "takeover_cooldown": DEFAULT_TAKEOVER_COOLDOWN,
    "takeover_oscillation_window": DEFAULT_TAKEOVER_OSCILLATION_WINDOW,
    "takeover_oscillation_threshold": DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD,
    "takeover_quarantine": DEFAULT_TAKEOVER_QUARANTINE,
    "lease_offline_ttl": DEFAULT_LEASE_OFFLINE_TTL,
    "shutdown_close_timeout": DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
    "enable_metrics": False,
    "auth_timeout": DEFAULT_AUTH_TIMEOUT,
    "metrics_token": None,
    "metrics_query_token_ok": False,
    "allowed_origins": (),
    "advertised_host": None,
    "insecure_off_loopback": False,
    "insecure_plaintext_at_rest": False,
    "clock": None,
    "protected_write_policies": None,
    "per_message_auth_keys": None,
    "require_per_message_auth": False,
    "per_message_auth_window_seconds": DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    "per_message_auth_replay_capacity": 4096,
    "per_message_auth_replay_store": None,
    "per_message_auth_sequence_floor_mode": SequenceFloorMode.OFF,
    "signed_event_trust_bundle": None,
    "capability_card_trust_bundle": None,
    "acl_policy": None,
    "require_acl": False,
    "role_grants": None,
    "require_role_claim": False,
    "require_fencing_epoch": False,
    "identity_trust_bundle": None,
    "require_identity_binding": False,
    "identity_pin_path": None,
    "identity_enrollment_path": None,
    "identity_enrollment_namespaces": (),
    "identity_enrollment_rate": DEFAULT_ENROLLMENT_RATE,
    "identity_enrollment_window_seconds": DEFAULT_ENROLLMENT_WINDOW_SECONDS,
    "private_directed_messages": False,
    "warn_stale_recipients": DEFAULT_WARN_STALE_RECIPIENTS,
    "recipient_liveness_window": DEFAULT_RECIPIENT_LIVENESS_WINDOW,
    "waiter_liveness_window": DEFAULT_WAITER_LIVENESS_WINDOW,
    "multihub_serving_policy": None,
    "spend_ledger": None,
    "namespace_ownership": None,
    "claim_peers": None,
    "claim_forwarder": forward_claim,
    "relay_peers": None,
    "relay_forwarder": relay_operator_action,
    "message_peers": None,
    "message_forwarder": forward_message,
    "message_forward_ttl": DEFAULT_FORWARD_TTL_SECONDS,
    "require_relay_reason": False,
    "require_two_person_relay": False,
    "observed_asserting_hubs": None,
    "federation_bundle": None,
    "federation_cert_source": live_peer_certificate_der,
    "federation_offer_path": None,
    "anti_rollback_checkpoint": True,
    "checkpoint_store_path": None,
    "checkpoint_interval": DEFAULT_CHECKPOINT_INTERVAL,
}
