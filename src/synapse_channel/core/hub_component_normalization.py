# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — normalize configuration at the composition boundary
"""Retain the constructor's coercions without mutating the caller's records."""

from __future__ import annotations

from dataclasses import replace

from synapse_channel.core.agent_liveness import (
    DEFAULT_RECIPIENT_LIVENESS_WINDOW,
    DEFAULT_WAITER_LIVENESS_WINDOW,
)
from synapse_channel.core.dead_letter_escalation import DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_defaults import (
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_COMPACT_HINT_THRESHOLD,
    DEFAULT_MAX_FINDINGS_PER_AGENT,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_MSG_BYTES,
    DEFAULT_RELAY_MAX_LINES,
    DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
)
from synapse_channel.core.hub_handshake import normalise_allow_origins
from synapse_channel.core.message_forward_origin import DEFAULT_FORWARD_TTL_SECONDS
from synapse_channel.core.numeric_coercion import safe_float, safe_int


def normalize_transport(config: HubConfig) -> HubConfig:
    """Normalize connection authentication and HTTP exposure settings."""
    auth = config.auth
    metrics = config.metrics
    return replace(
        config,
        auth=replace(
            auth,
            auth_timeout=max(safe_float(auth.auth_timeout, default=DEFAULT_AUTH_TIMEOUT), 0.1),
            insecure_off_loopback=bool(auth.insecure_off_loopback),
            insecure_plaintext_at_rest=bool(auth.insecure_plaintext_at_rest),
            require_per_message_auth=bool(auth.require_per_message_auth),
            require_acl=bool(auth.require_acl),
            require_role_claim=bool(auth.require_role_claim),
            require_fencing_epoch=bool(auth.require_fencing_epoch),
            require_identity_binding=bool(auth.require_identity_binding),
            private_directed_messages=bool(auth.private_directed_messages),
        ),
        metrics=replace(
            metrics,
            enable_metrics=bool(metrics.enable_metrics),
            metrics_token=metrics.metrics_token or None,
            metrics_query_token_ok=bool(metrics.metrics_query_token_ok),
            allowed_origins=normalise_allow_origins(tuple(metrics.allowed_origins or ())),
            advertised_host=(metrics.advertised_host or "").strip() or None,
        ),
    )


def normalize_liveness_and_routing(config: HubConfig) -> HubConfig:
    """Normalize liveness windows and multi-hub routing posture."""
    live = config.liveness
    routing = config.multihub
    return replace(
        config,
        liveness=replace(
            live,
            warn_stale_recipients=bool(live.warn_stale_recipients),
            recipient_liveness_window=max(
                safe_float(
                    live.recipient_liveness_window, default=DEFAULT_RECIPIENT_LIVENESS_WINDOW
                ),
                0.0,
            ),
            waiter_liveness_window=max(
                safe_float(live.waiter_liveness_window, default=DEFAULT_WAITER_LIVENESS_WINDOW),
                0.0,
            ),
        ),
        multihub=replace(
            routing,
            message_forward_ttl=max(
                1.0, safe_float(routing.message_forward_ttl, default=DEFAULT_FORWARD_TTL_SECONDS)
            ),
            require_relay_reason=bool(routing.require_relay_reason),
            require_two_person_relay=bool(routing.require_two_person_relay),
        ),
    )


def normalize_limits(config: HubConfig, clients: HubClientRegistry) -> HubConfig:
    """Apply retention bounds and the actual registry's admission normalization."""
    limits = config.limits
    return replace(
        config,
        shutdown_close_timeout=max(
            safe_float(config.shutdown_close_timeout, default=DEFAULT_SHUTDOWN_CLOSE_TIMEOUT), 0.1
        ),
        relay_max_lines=safe_int(
            config.relay_max_lines, default=DEFAULT_RELAY_MAX_LINES, min_value=1
        ),
        limits=replace(
            limits,
            max_clients=clients.max_clients,
            max_connections_per_host=clients.max_connections_per_host,
            max_msg_bytes=safe_int(
                limits.max_msg_bytes, default=DEFAULT_MAX_MSG_BYTES, min_value=1
            ),
            max_history=safe_int(limits.max_history, default=DEFAULT_MAX_HISTORY, min_value=1),
            max_findings_per_agent=safe_int(
                limits.max_findings_per_agent, default=DEFAULT_MAX_FINDINGS_PER_AGENT, min_value=1
            ),
            compact_hint_threshold=safe_int(
                limits.compact_hint_threshold, default=DEFAULT_COMPACT_HINT_THRESHOLD, min_value=1
            ),
            dead_letter_escalation_threshold=safe_int(
                limits.dead_letter_escalation_threshold,
                default=DEFAULT_DEAD_LETTER_ESCALATION_THRESHOLD,
                min_value=0,
            ),
            board_task_cap=(
                safe_int(limits.board_task_cap, default=1, min_value=1)
                if limits.board_task_cap is not None
                else None
            ),
        ),
        takeover=replace(
            config.takeover,
            takeover_cooldown=clients.takeover_cooldown,
            takeover_oscillation_window=clients.takeover_oscillation_window,
            takeover_oscillation_threshold=clients.takeover_oscillation_threshold,
            takeover_quarantine=clients.takeover_quarantine,
            lease_offline_ttl=clients.ownership.offline_ttl,
        ),
    )
