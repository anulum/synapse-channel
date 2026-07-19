# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — secure umbrella hub profile
"""Secure-mode umbrella profile for multi-seat production hubs.

``synapse hub --secure`` is the strict multi-seat production preset. It composes
the existing :func:`~synapse_channel.core.team_secure.apply_team_secure_hub_profile`
trust profile and :func:`~synapse_channel.core.paranoid.apply_paranoid_hub_profile`
exposed-hub profile, then adds bounded per-agent, per-host, and per-host-connection
flood limits. It generates no credentials, reuses no secret, and enables no metrics
surface; missing operator material fails closed before any socket or durable store
is opened, and an explicitly stricter limit is always preserved.

The two subordinate profiles remain the single authority for their own checks; this
module only turns both on, aggregates their missing material into one error, applies
the flood ceilings, and emits one consolidated report instead of two.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.hub import DEFAULT_MAX_CONNECTIONS_PER_HOST
from synapse_channel.core.paranoid import apply_paranoid_hub_profile
from synapse_channel.core.team_secure import apply_team_secure_hub_profile

#: Per-agent message rate ceiling (messages/second) and burst applied when the
#: parser value is disabled. A stricter positive value is preserved.
SECURE_AGENT_RATE: float = 100.0
SECURE_AGENT_BURST: float = 20.0
#: Per-principal durable chat ingress (events / serialized frame bytes / window seconds)
#: applied when --secure leaves the durable-ingress flags disabled.
SECURE_DURABLE_INGRESS_EVENTS: int = 100
SECURE_DURABLE_INGRESS_BYTES: int = 1_048_576
SECURE_DURABLE_INGRESS_WINDOW: float = 60.0
#: Per-host frame rate ceiling (frames/second) and burst applied when disabled.
SECURE_HOST_RATE: float = 500.0
SECURE_HOST_BURST: float = 100.0
#: Simultaneous connections per host applied when the parser value is disabled.
SECURE_MAX_CONNECTIONS_PER_HOST: int = 10


class SecureModeError(SynapseError, ValueError):
    """Raised when a hub cannot satisfy secure-mode requirements."""

    code = "secure_mode"


@dataclass(frozen=True)
class SecureHubReport:
    """Effective secure-mode hub posture.

    Attributes
    ----------
    enforced : tuple[str, ...]
        Composed gates and normalised limits the preset required before startup.
    effective_limits : tuple[str, ...]
        The exact per-agent, per-host, and connection ceilings in force.
    missing_hooks : tuple[str, ...]
        Controls the preset genuinely does not compose, derived from the paranoid
        profile's list with the entries this umbrella enforces filtered out (the
        paranoid report suggests composing team-secure for identity verification;
        the umbrella already does). The list is never empty, so the report never
        claims "all security".
    """

    enforced: tuple[str, ...]
    effective_limits: tuple[str, ...]
    missing_hooks: tuple[str, ...]

    def stderr_lines(self) -> tuple[str, ...]:
        """Return human-readable report lines for the operator."""
        return (
            f"secure mode enforced: {', '.join(self.enforced)}",
            f"secure mode effective limits: {', '.join(self.effective_limits)}",
            f"secure mode missing hooks: {', '.join(self.missing_hooks)}",
        )


def _missing_material(args: argparse.Namespace) -> list[str]:
    """Return every required operator input that is absent, for one aggregate error."""
    missing: list[str] = []
    if not getattr(args, "token", None):
        missing.append("--token or --token-file (connect-token authentication)")
    if not getattr(args, "db", None):
        missing.append("--db (durable event log)")
    if not str(getattr(args, "identity_trust", "") or "").strip():
        missing.append("--identity-trust (Ed25519 connection identity binding)")
    if not str(getattr(args, "role_grants", "") or "").strip():
        missing.append("--role-grants (deny-by-default role store)")
    if not getattr(args, "message_auth_key", None):
        missing.append(
            "--message-auth-key or --message-auth-key-file "
            "(sender-bound per-message authentication)"
        )
    if not str(getattr(args, "acl_policy", "") or "").strip():
        missing.append("--acl-policy (deny-by-default ACL enforcement)")
    if not getattr(args, "tls_certfile", None) or not getattr(args, "tls_keyfile", None):
        missing.append("--tls-certfile and --tls-keyfile (native WSS)")
    if bool(getattr(args, "metrics", False)) and not getattr(args, "metrics_token", None):
        missing.append(
            "--metrics-token or --metrics-token-file (bearer auth for the enabled metrics surface)"
        )
    return missing


def _apply_rate_ceiling(
    args: argparse.Namespace,
    *,
    value_attr: str,
    burst_attr: str,
    flag: str,
    burst_flag: str,
    ceiling: float,
    burst_ceiling: float,
    default: float,
    default_burst: float,
) -> str:
    """Normalise one rate limit and its burst under the preset; report the result.

    A disabled value (``0``) receives the named preset default and burst; a positive
    value at or below the ceiling is preserved as stricter; a positive value above
    the ceiling fails closed rather than silently weakening the named posture. The
    burst is held to the same discipline: a non-finite rate or burst fails closed
    (``nan`` compares false against every ceiling and would then construct no
    limiter at all downstream), a burst above its ceiling fails closed, and a
    disabled burst beside a stricter rate receives the preset default so the token
    bucket is never unbounded or absent.
    """
    current = float(getattr(args, value_attr, 0.0) or 0.0)
    if not math.isfinite(current):
        raise SecureModeError(
            f"secure mode requires a finite {flag}; {current!r} would bypass the "
            f"flood ceiling — pass a real number at or below {ceiling:g}, or drop "
            "it to use the preset default"
        )
    if current <= 0.0:
        setattr(args, value_attr, default)
        setattr(args, burst_attr, default_burst)
        return f"{default:g}/s burst {default_burst:g}"
    if current > ceiling:
        raise SecureModeError(
            f"secure mode caps {flag} at {ceiling:g}; {current:g} would weaken the "
            f"secure posture — lower {flag} to {ceiling:g} or less, or drop it to use "
            "the preset default"
        )
    burst = float(getattr(args, burst_attr, 0.0) or 0.0)
    if not math.isfinite(burst):
        raise SecureModeError(
            f"secure mode requires a finite {burst_flag}; {burst!r} would allow an "
            f"unbounded flood burst — pass a real number at or below {burst_ceiling:g}, "
            "or drop it to use the preset default"
        )
    if burst > burst_ceiling:
        raise SecureModeError(
            f"secure mode caps {burst_flag} at {burst_ceiling:g}; {burst:g} would "
            f"weaken the secure posture — lower {burst_flag} to {burst_ceiling:g} or "
            "less, or drop it to use the preset default"
        )
    if burst <= 0.0:
        burst = default_burst
        setattr(args, burst_attr, default_burst)
    return f"{current:g}/s burst {burst:g} (operator-stricter)"


def apply_secure_hub_profile(args: argparse.Namespace) -> SecureHubReport | None:
    """Validate and normalise ``synapse hub --secure`` settings.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed hub arguments. When the switch is on the function turns both the
        team-secure and paranoid profiles on, aggregates missing material into one
        error, applies the flood ceilings, and returns the consolidated report.

    Returns
    -------
    SecureHubReport or None
        Consolidated report when the preset is enabled; ``None`` when it is off so
        the caller's existing per-profile flow runs unchanged.

    Raises
    ------
    SecureModeError
        If required operator material is absent or a configured limit exceeds a
        preset ceiling. No durable store is opened and no socket binds first.
    """
    if not bool(getattr(args, "secure", False)):
        return None

    missing = _missing_material(args)
    if missing:
        raise SecureModeError(
            "secure mode requires all production material before startup; missing: "
            + "; ".join(missing)
        )

    # Force the enforcement gates the subordinate profiles require as preconditions,
    # then compose them. team-secure forces identity binding, role claims, and
    # private directed messages; paranoid requires per-message auth and ACL
    # enforcement already on, so the umbrella turns them on here.
    args.team_secure = True
    args.paranoid = True
    args.require_message_auth = True
    args.require_acl = True
    team_report = apply_team_secure_hub_profile(args)
    paranoid_report = apply_paranoid_hub_profile(args)
    assert team_report is not None and paranoid_report is not None

    effective_limits = (
        "per-agent "
        + _apply_rate_ceiling(
            args,
            value_attr="rate",
            burst_attr="burst",
            flag="--rate",
            burst_flag="--burst",
            ceiling=SECURE_AGENT_RATE,
            burst_ceiling=SECURE_AGENT_BURST,
            default=SECURE_AGENT_RATE,
            default_burst=SECURE_AGENT_BURST,
        ),
        "per-host "
        + _apply_rate_ceiling(
            args,
            value_attr="host_rate",
            burst_attr="host_burst",
            flag="--host-rate",
            burst_flag="--host-burst",
            ceiling=SECURE_HOST_RATE,
            burst_ceiling=SECURE_HOST_BURST,
            default=SECURE_HOST_RATE,
            default_burst=SECURE_HOST_BURST,
        ),
        "connections/host " + _apply_connection_ceiling(args),
        "durable-ingress " + _apply_durable_ingress_ceiling(args),
    )

    # Both profiles require a connect token, so dedupe the shared line while keeping
    # the composed order.
    composed = (
        *paranoid_report.enforced,
        *team_report.enforced,
        "bounded flood limits",
        "bounded durable-ingress quotas",
    )
    enforced = tuple(dict.fromkeys(composed))
    # The paranoid report lists hooks IT does not compose; any entry whose stated
    # remedy is composing team-secure is enforced by this umbrella, so copying it
    # verbatim would contradict the report's own enforced lines. Everything else
    # (at-rest encryption, mutual TLS, private channels, …) stays honestly missing.
    missing_hooks = tuple(
        hook for hook in paranoid_report.missing_hooks if "--team-secure" not in hook
    )
    return SecureHubReport(
        enforced=enforced,
        effective_limits=effective_limits,
        missing_hooks=missing_hooks,
    )


def _apply_durable_ingress_ceiling(args: argparse.Namespace) -> str:
    """Apply fail-closed durable-ingress ceilings under ``--secure``."""
    raw_events = getattr(args, "durable_ingress_events", 0)
    raw_bytes = getattr(args, "durable_ingress_bytes", 0)
    raw_window = getattr(args, "durable_ingress_window", 0.0)
    try:
        events = int(raw_events or 0)
        nbytes = int(raw_bytes or 0)
        window = float(raw_window or 0.0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SecureModeError(
            "secure mode requires numeric --durable-ingress-events, "
            "--durable-ingress-bytes, and --durable-ingress-window values"
        ) from exc
    if not math.isfinite(window):
        raise SecureModeError(
            f"secure mode requires a finite --durable-ingress-window; got {raw_window!r}"
        )
    operator_supplied = bool(
        events > 0 or nbytes > 0 or (window > 0.0 and window != SECURE_DURABLE_INGRESS_WINDOW)
    )
    if events <= 0:
        args.durable_ingress_events = SECURE_DURABLE_INGRESS_EVENTS
        events = SECURE_DURABLE_INGRESS_EVENTS
    if nbytes <= 0:
        args.durable_ingress_bytes = SECURE_DURABLE_INGRESS_BYTES
        nbytes = SECURE_DURABLE_INGRESS_BYTES
    if window <= 0.0:
        args.durable_ingress_window = SECURE_DURABLE_INGRESS_WINDOW
        window = SECURE_DURABLE_INGRESS_WINDOW
    if events > SECURE_DURABLE_INGRESS_EVENTS:
        raise SecureModeError(
            f"secure mode caps --durable-ingress-events at "
            f"{SECURE_DURABLE_INGRESS_EVENTS}; got {events}"
        )
    if nbytes > SECURE_DURABLE_INGRESS_BYTES:
        raise SecureModeError(
            f"secure mode caps --durable-ingress-bytes at "
            f"{SECURE_DURABLE_INGRESS_BYTES}; got {nbytes}"
        )
    if window < SECURE_DURABLE_INGRESS_WINDOW:
        raise SecureModeError(
            f"secure mode requires --durable-ingress-window at least "
            f"{SECURE_DURABLE_INGRESS_WINDOW:g}s; got {window:g}s"
        )
    suffix = " (operator)" if operator_supplied else ""
    return f"{events} events / {nbytes} B / {window:g}s{suffix}"


def _apply_connection_ceiling(args: argparse.Namespace) -> str:
    """Normalise the per-host connection cap under the preset and report it."""
    raw = getattr(args, "max_connections_per_host", 0)
    try:
        current = int(raw or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        # int(nan) raises ValueError and int(inf) raises OverflowError; a value the
        # runtime cannot count with must fail closed, not crash or slip through.
        raise SecureModeError(
            f"secure mode requires an integer --max-connections-per-host; got {raw!r}"
        ) from exc
    if current <= 0:
        args.max_connections_per_host = SECURE_MAX_CONNECTIONS_PER_HOST
        return f"{SECURE_MAX_CONNECTIONS_PER_HOST}"
    if current > SECURE_MAX_CONNECTIONS_PER_HOST:
        # The open-hub parser default is deliberately looser than the secure
        # ceiling. Inherit-and-clamp so ``synapse hub --secure`` does not fail
        # solely because the operator left the open default in place; any other
        # explicit value above the ceiling still fails closed.
        if current == DEFAULT_MAX_CONNECTIONS_PER_HOST:
            args.max_connections_per_host = SECURE_MAX_CONNECTIONS_PER_HOST
            return f"{SECURE_MAX_CONNECTIONS_PER_HOST} (clamped from open default {current})"
        raise SecureModeError(
            f"secure mode caps --max-connections-per-host at "
            f"{SECURE_MAX_CONNECTIONS_PER_HOST}; {current} would weaken the secure "
            f"posture — lower it to {SECURE_MAX_CONNECTIONS_PER_HOST} or less, or drop "
            "it to use the preset default"
        )
    return f"{current} (operator-stricter)"
