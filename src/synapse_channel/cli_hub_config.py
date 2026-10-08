# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — CLI arguments into canonical hub configuration families
"""Build grouped hub settings from resolved CLI arguments and loaded resources.

Loading credentials, opening resources and applying security presets belong
to command startup. These builders only project those resolved inputs into
the existing immutable configuration families, preserving their defaults.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from synapse_channel.core.hub_config import (
    HubAuthConfig,
    HubConfig,
    HubLimits,
    HubLiveness,
    HubMetricsConfig,
    MultiHubConfig,
    TakeoverDamping,
)
from synapse_channel.core.hub_defaults import DEFAULT_MAX_CONNECTIONS_PER_HOST
from synapse_channel.core.identity_enrollments import (
    DEFAULT_ENROLLMENT_RATE,
    DEFAULT_ENROLLMENT_WINDOW_SECONDS,
)


def resolve_max_connections_per_host(raw: int | None) -> int | None:
    """Resolve the CLI's omitted, disabled or positive host connection ceiling."""
    if raw is None:
        return DEFAULT_MAX_CONNECTIONS_PER_HOST
    if raw <= 0:
        return None
    return int(raw)


def _limits(args: argparse.Namespace, base: HubLimits) -> HubLimits:
    """Project retention and admission limits, preserving unexposed defaults."""
    return replace(
        base,
        max_history=args.max_history,
        max_progress=args.max_progress,
        max_progress_per_author=args.max_progress_per_author,
        max_progress_per_task=args.max_progress_per_task,
        board_task_cap=args.board_task_cap,
        max_findings_per_agent=args.max_findings_per_agent,
        max_clients=args.max_clients,
        max_unauth_clients=args.max_unauth_clients,
        max_connections_per_host=resolve_max_connections_per_host(
            getattr(args, "max_connections_per_host", None)
        ),
        max_msg_bytes=args.max_msg_kb * 1024,
        max_claims_per_agent=args.max_claims_per_agent,
        max_offers_per_agent=args.max_offers_per_agent,
        max_paths_per_claim=args.max_paths_per_claim,
        compact_hint_threshold=args.compact_hint_threshold,
    )


def _authentication(args: argparse.Namespace, base: HubAuthConfig) -> HubAuthConfig:
    """Project authentication controls around already-loaded trust resources."""
    return replace(
        base,
        auth_timeout=args.auth_timeout,
        require_per_message_auth=args.require_message_auth,
        per_message_auth_window_seconds=args.message_auth_window_seconds,
        per_message_auth_replay_capacity=args.message_auth_replay_capacity,
        require_acl=args.require_acl,
        require_role_claim=args.require_role_claim,
        require_fencing_epoch=bool(getattr(args, "require_fencing_epoch", False)),
        require_identity_binding=args.require_identity_binding,
        identity_pin_path=args.identity_pins or None,
        identity_enrollment_path=getattr(args, "identity_enrollments", "") or None,
        identity_enrollment_namespaces=tuple(
            getattr(args, "identity_enrollment_namespace", []) or ()
        ),
        identity_enrollment_rate=getattr(args, "identity_enrollment_rate", DEFAULT_ENROLLMENT_RATE),
        identity_enrollment_window_seconds=getattr(
            args, "identity_enrollment_window", DEFAULT_ENROLLMENT_WINDOW_SECONDS
        ),
        private_directed_messages=args.private_directed_messages,
        insecure_off_loopback=args.insecure_off_loopback,
        insecure_plaintext_at_rest=getattr(args, "insecure_plaintext_at_rest", False),
    )


def _metrics(args: argparse.Namespace, base: HubMetricsConfig) -> HubMetricsConfig:
    """Project HTTP observability and handshake posture without exposing secrets."""
    return replace(
        base,
        enable_metrics=args.metrics,
        metrics_token=args.metrics_token,
        metrics_query_token_ok=args.metrics_query_token_ok,
        allowed_origins=tuple(getattr(args, "allow_origin", ()) or ()),
        advertised_host=getattr(args, "advertised_host", None) or None,
    )


def _liveness(args: argparse.Namespace, base: HubLiveness) -> HubLiveness:
    """Project the directed-recipient and receiver-sidecar liveness windows."""
    return replace(
        base,
        warn_stale_recipients=args.warn_stale_recipients,
        recipient_liveness_window=args.recipient_liveness_window,
        waiter_liveness_window=args.waiter_liveness_window,
    )


def _takeover(args: argparse.Namespace, base: TakeoverDamping) -> TakeoverDamping:
    """Project CLI name-ownership controls around existing damping defaults."""
    return replace(
        base,
        takeover_cooldown=args.takeover_cooldown,
        lease_offline_ttl=args.lease_offline_ttl,
    )


def _multihub(args: argparse.Namespace, base: MultiHubConfig) -> MultiHubConfig:
    """Project forwarding posture while retaining loaded peer routes and policies."""
    return replace(
        base,
        message_forward_ttl=getattr(args, "message_forward_ttl", 86_400.0),
        require_relay_reason=getattr(args, "require_relay_reason", False),
        require_two_person_relay=getattr(args, "require_two_person_relay", False),
    )


def build_cli_hub_config(args: argparse.Namespace, loaded: HubConfig) -> HubConfig:
    """Build one canonical record from the resolved CLI and actual loaded resources.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments after secret-file resolution and security profiles.
    loaded : HubConfig
        Already-open resources and already-validated trust/peer material.

    Returns
    -------
    HubConfig
        Immutable family records preserving all unexposed library defaults.
    """
    return replace(
        loaded,
        hub_id=args.hub_id,
        relay_log=args.relay_log,
        relay_max_lines=args.relay_max_lines,
        checkpoint_interval=args.checkpoint_interval,
        shutdown_close_timeout=args.shutdown_close_timeout,
        limits=_limits(args, loaded.limits),
        auth=_authentication(args, loaded.auth),
        metrics=_metrics(args, loaded.metrics),
        liveness=_liveness(args, loaded.liveness),
        takeover=_takeover(args, loaded.takeover),
        multihub=_multihub(args, loaded.multihub),
        federation=replace(loaded.federation, federation_offer_path=args.federation_offer or None),
    )
