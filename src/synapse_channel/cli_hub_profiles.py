# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI hub command
"""Normalize CLI security profiles and ingress limits."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from synapse_channel.cli_hub_startup import HubStartup, HubStartupRefused
from synapse_channel.core.paranoid import ParanoidModeError, apply_paranoid_hub_profile
from synapse_channel.core.rate_policy import (
    HubExposurePosture,
    RateLimits,
    decide_auto_rate_policy,
    is_loopback_bind,
)
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.secret_files import SecretFileError, read_secret_file, read_secret_lines
from synapse_channel.core.secure import SecureModeError, apply_secure_hub_profile
from synapse_channel.core.team_secure import TeamSecureModeError, apply_team_secure_hub_profile


def _resolve_file_backed_secrets(args: argparse.Namespace) -> None:
    """Fold the owner-only ``*-file`` secret companions into their argv fields.

    An explicit ``--metrics-token`` wins over its file, mirroring the global
    ``--token``/``--token-file`` precedence; ``--message-auth-key-file`` entries
    merge after any argv keys so both sources can rotate together. Runs before
    the hardening presets, so file-delivered material satisfies their presence
    checks exactly as argv material does.
    """
    metrics_token_file = getattr(args, "metrics_token_file", None)
    if metrics_token_file and (not args.metrics_token):
        args.metrics_token = read_secret_file(metrics_token_file, flag="--metrics-token-file")
    peer_token_file = getattr(args, "message_peer_token_file", None)
    if peer_token_file and (not getattr(args, "message_peer_token", None)):
        args.message_peer_token = read_secret_file(
            peer_token_file, flag="--message-peer-token-file"
        )
    key_file = getattr(args, "message_auth_key_file", None)
    if key_file:
        entries = read_secret_lines(key_file, flag="--message-auth-key-file")
        args.message_auth_key = [*args.message_auth_key, *entries]


def _hub_multi_seat_intent(args: argparse.Namespace) -> bool:
    """Return whether startup args declare multi-seat / multi-party intent.

    Runtime seat count is not known before clients connect, so multi-seat is an
    *intent* signal: explicit ``--expect-multi-seat``, multi-seat security
    profiles, identity/role material, or private directed routing.
    """
    if bool(getattr(args, "expect_multi_seat", False)):
        return True
    if bool(getattr(args, "team_secure", False) or getattr(args, "secure", False)):
        return True
    if bool(
        getattr(args, "require_role_claim", False)
        or getattr(args, "require_identity_binding", False)
        or getattr(args, "private_directed_messages", False)
    ):
        return True
    identity_trust = str(getattr(args, "identity_trust", "") or "").strip()
    role_grants = str(getattr(args, "role_grants", "") or "").strip()
    return bool(identity_trust or role_grants)


def _hub_bridge_exposed(args: argparse.Namespace) -> bool:
    """Return whether the operator declared an A2A/MCP bridge as exposed.

    Bridges run as separate processes today; the truthful signal is the explicit
    ``--bridge-exposed`` startup flag (operators who front a2a-serve/mcp against
    this hub must set it). No silent false positive on pure loopback single-seat.
    """
    return bool(getattr(args, "bridge_exposed", False))


def _apply_auto_rate_policy(args: argparse.Namespace) -> None:
    """Fill disabled flood limits when the hub starts in an exposed posture (REV-SEC-06).

    Pure decision from :mod:`synapse_channel.core.rate_policy` is applied back onto
    ``args`` so the existing limiter construction path stays unchanged. When
    ``--secure`` is active the decision stands down (secure already normalises
    limits). Local-first loopback single-seat hubs stay unbounded.
    """
    token_configured = bool(args.token or getattr(args, "token_file", None))
    multi_seat = _hub_multi_seat_intent(args)
    bridge_exposed = _hub_bridge_exposed(args)
    posture = HubExposurePosture(
        off_loopback_bind=not is_loopback_bind(args.host),
        token_configured=token_configured,
        bridge_exposed=bridge_exposed,
        multi_seat=multi_seat,
    )
    raw_host_cap = getattr(args, "max_connections_per_host", None)
    operator = RateLimits(
        agent_rate=float(args.rate),
        agent_burst=float(args.burst),
        host_rate=float(args.host_rate),
        host_burst=float(args.host_burst),
        max_connections_per_host=0 if raw_host_cap is None else int(raw_host_cap),
    )
    decision = decide_auto_rate_policy(
        posture, operator, secure_mode=bool(getattr(args, "secure", False))
    )
    if not decision.auto_enabled:
        return
    args.rate = decision.limits.agent_rate
    args.burst = decision.limits.agent_burst
    args.host_rate = decision.limits.host_rate
    args.host_burst = decision.limits.host_burst
    args.max_connections_per_host = decision.limits.max_connections_per_host
    for line in decision.report_lines:
        print(f"synapse hub: {line}", file=sys.stderr)


def apply_hub_profiles(ctx: HubStartup) -> None:
    """Resolve secrets and apply the existing security-profile precedence."""
    ctx.factories.logging(log_format=ctx.args.log_format, level=ctx.args.log_level)
    try:
        _resolve_file_backed_secrets(ctx.args)
    except SecretFileError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None
    try:
        secure_report = apply_secure_hub_profile(ctx.args)
    except SecureModeError as exc:
        print(f"synapse hub: {exc}", file=sys.stderr)
        raise HubStartupRefused() from None
    if secure_report is not None:
        for line in secure_report.stderr_lines():
            print(f"synapse hub: {line}", file=sys.stderr)
    else:
        try:
            paranoid_report = apply_paranoid_hub_profile(ctx.args)
        except ParanoidModeError as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None
        if paranoid_report is not None:
            for line in paranoid_report.stderr_lines():
                print(f"synapse hub: {line}", file=sys.stderr)
        try:
            team_secure_report = apply_team_secure_hub_profile(ctx.args)
        except TeamSecureModeError as exc:
            print(f"synapse hub: {exc}", file=sys.stderr)
            raise HubStartupRefused() from None
        if team_secure_report is not None:
            for line in team_secure_report.stderr_lines():
                print(f"synapse hub: {line}", file=sys.stderr)
    _apply_auto_rate_policy(ctx.args)


def configure_ingress_limits(ctx: HubStartup) -> None:
    """Build the existing per-agent, per-host and durable ingress limits."""
    ctx.config = replace(
        ctx.config,
        rate_limiter=RateLimiter(rate_per_second=ctx.args.rate, burst=ctx.args.burst)
        if ctx.args.rate > 0
        else None,
    )
    ctx.config = replace(
        ctx.config,
        host_rate_limiter=RateLimiter(rate_per_second=ctx.args.host_rate, burst=ctx.args.host_burst)
        if ctx.args.host_rate > 0
        else None,
    )
    ctx.config = replace(ctx.config, durable_ingress_quota=None)
    ingress_events = int(getattr(ctx.args, "durable_ingress_events", 0) or 0)
    ingress_bytes = int(getattr(ctx.args, "durable_ingress_bytes", 0) or 0)
    if not getattr(ctx.args, "no_durable_ingress_quota", False):
        from synapse_channel.core.durable_ingress import (
            DEFAULT_MAX_BYTES,
            DEFAULT_MAX_EVENTS,
            DurableIngressQuota,
        )

        ctx.config = replace(
            ctx.config,
            durable_ingress_quota=DurableIngressQuota(
                max_events=ingress_events if ingress_events > 0 else DEFAULT_MAX_EVENTS,
                max_bytes=ingress_bytes if ingress_bytes > 0 else DEFAULT_MAX_BYTES,
                window_seconds=float(getattr(ctx.args, "durable_ingress_window", 60.0) or 60.0),
            ),
        )
