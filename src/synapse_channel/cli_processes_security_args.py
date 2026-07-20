# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub security CLI arguments
"""Register the hub's authentication, authorisation, and transport options."""

from __future__ import annotations

import argparse

from synapse_channel.cli_deprecated_options import (
    METRICS_QUERY_FLAG_REMOVAL_VERSION,
    DeprecatedMetricsQueryTokenAction,
)
from synapse_channel.core.capability_card_trust import (
    DEFAULT_CAPABILITY_CARD_CLOCK_SKEW_SECONDS,
    DEFAULT_CAPABILITY_CARD_HISTORY_CAPACITY,
    DEFAULT_CAPABILITY_CARD_HISTORY_RETENTION_SECONDS,
)
from synapse_channel.core.hub import DEFAULT_AUTH_TIMEOUT
from synapse_channel.core.message_auth import DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS


def add_hub_security_arguments(hub: argparse.ArgumentParser) -> None:
    """Add security-profile and security-material options to the hub parser.

    Parameters
    ----------
    hub : argparse.ArgumentParser
        Production ``synapse hub`` parser receiving the options.
    """
    hub.add_argument(
        "--tls-certfile",
        default=None,
        help="PEM certificate chain for native WSS; requires --tls-keyfile.",
    )
    hub.add_argument(
        "--tls-keyfile",
        default=None,
        help="PEM private key for native WSS; requires --tls-certfile.",
    )
    hub.add_argument(
        "--paranoid",
        action="store_true",
        help="Require a strict local hub profile and print missing hardening hooks.",
    )
    hub.add_argument(
        "--team-secure",
        action="store_true",
        help="Multi-seat trust profile: require a connect token, identity binding "
        "(--identity-trust), role-claim grants (--role-grants), and private directed "
        "messages. Lighter than --paranoid (no TLS/ACL/HMAC mandate); combine both "
        "when a multi-seat hub is also network-exposed. Fails closed if material is "
        "missing; prints recommended next hardening steps on stderr.",
    )
    hub.add_argument(
        "--secure",
        action="store_true",
        help="Strict multi-seat production umbrella: compose --team-secure and "
        "--paranoid, then bound per-agent (100/s), per-host (500/s), and per-host "
        "connection (10) flood limits. Requires a token, --db, --identity-trust, "
        "--role-grants, a --message-auth-key, --acl-policy, and --tls-certfile/"
        "--tls-keyfile (plus --metrics-token when --metrics is on); fails closed "
        "listing all missing material at once. A stricter positive limit is kept; a "
        "limit above a preset ceiling is refused. Prints one consolidated report.",
    )
    hub.add_argument(
        "--token",
        default=None,
        help="Require this shared-secret token from connecting agents (off by default).",
    )
    hub.add_argument(
        "--metrics",
        action="store_true",
        help="Also serve HTTP GET /metrics (Prometheus) and /health on the same port.",
    )
    hub.add_argument(
        "--auth-timeout",
        type=float,
        default=DEFAULT_AUTH_TIMEOUT,
        help="On a secured hub (--token), seconds to wait for an authenticated first "
        "frame before closing the socket (no welcome/roster until then).",
    )
    hub.add_argument(
        "--metrics-token",
        default=None,
        help="Require this token (Authorization: Bearer) for /metrics and /health, so "
        "an exposed endpoint does not leak metadata (off by default).",
    )
    hub.add_argument(
        "--metrics-token-file",
        default=None,
        metavar="PATH",
        help="Read the metrics bearer token from this owner-only (chmod 600) file "
        "instead of --metrics-token; prefer it for a real secret — an argv value "
        "is visible to anyone running `ps`. An explicit --metrics-token wins.",
    )
    hub.add_argument(
        "--metrics-query-token-ok",
        action=DeprecatedMetricsQueryTokenAction,
        default=False,
        help="Deprecated: also accept the metrics token as a ?token= query parameter "
        f"until {METRICS_QUERY_FLAG_REMOVAL_VERSION} (off by default; a query token can "
        "leak into logs, history, "
        "and proxy records). "
        "Loopback-only: binding a non-loopback host with this set is refused unless "
        "--insecure-off-loopback is also passed.",
    )
    hub.add_argument(
        "--message-auth-key",
        action="append",
        default=[],
        metavar="KEY_ID:SECRET:SENDER[,SENDER...]",
        help="Enable a sender-bound per-message HMAC key for signed mutating frames; "
        "repeat for rotation. Off by default.",
    )
    hub.add_argument(
        "--message-auth-key-file",
        default=None,
        metavar="PATH",
        help="Read KEY_ID:SECRET:SENDER[,SENDER...] entries (one per line, # comments "
        "allowed) from this owner-only (chmod 600) file; prefer it for real secrets — "
        "an argv value is visible to anyone running `ps`. Entries merge with any "
        "--message-auth-key values for rotation.",
    )
    hub.add_argument(
        "--require-message-auth",
        action="store_true",
        help="Require signed per-message authentication on mutating frames when "
        "--message-auth-key is configured. Off by default for compatibility.",
    )
    hub.add_argument(
        "--message-auth-window-seconds",
        type=float,
        default=DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
        help="Timestamp window accepted for per-message authentication frames.",
    )
    hub.add_argument(
        "--message-auth-replay-capacity",
        type=int,
        default=4096,
        help="Maximum live nonce entries retained for replay detection in the "
        "process-local cache and optional durable ledger.",
    )
    hub.add_argument(
        "--message-auth-replay-db",
        default=None,
        metavar="PATH",
        help="Crash-durable authenticated-frame nonce ledger. With "
        "--require-message-auth and --db, defaults to <DB>.message-auth.db. "
        "An explicit path is required for a durable in-memory hub.",
    )
    hub.add_argument(
        "--message-auth-sequence-floor-mode",
        choices=("off", "compat", "strict"),
        default="off",
        help="Durable per-key/sender sequence policy. 'off' persists nonces only; "
        "'compat' records a high-water mark without rejecting lower fresh nonces; "
        "'strict' rejects sequence values at or below the durable floor.",
    )
    hub.add_argument(
        "--acl-policy",
        default="",
        metavar="FILE",
        help="Deny-by-default ACL policy JSON to authorise mutating frames. "
        "Loaded but not enforced unless --require-acl is set.",
    )
    hub.add_argument(
        "--require-acl",
        action="store_true",
        help="Reject mutating frames the sender's identity is not allowed to send by "
        "--acl-policy. Off by default; read/query verbs and a missing policy still pass. "
        "Namespace authorisation is only as strong as the sender binding, so pair this "
        "with --token and --require-message-auth on an exposed hub.",
    )
    hub.add_argument(
        "--role-grants",
        default="",
        metavar="FILE",
        help="Deny-by-default role-grant store JSON (written by `synapse role`) naming "
        "which identities may claim which roles. Loaded but not enforced unless "
        "--require-role-claim is set.",
    )
    hub.add_argument(
        "--require-role-claim",
        action="store_true",
        help="Bind a heartbeat's declared role only when --role-grants authorises the "
        "identity for it; an unauthorised role is dropped instead of squatted. Off by "
        "default, so an open hub binds declared roles unchanged. The gate keys off the "
        "self-reported identity, so pair this with --token and --require-message-auth on "
        "an exposed hub.",
    )
    hub.add_argument(
        "--identity-trust",
        default="",
        metavar="FILE",
        help="Identity trust bundle JSON (Ed25519 public keys bound to audit subjects) "
        "used to verify a socket's signed registration. Separate key material from "
        "federation and signed-event trust. Enforced only with --require-identity-binding.",
    )
    hub.add_argument(
        "--require-identity-binding",
        action="store_true",
        help="Require a socket's first frame to carry a valid identity signature verified "
        "against --identity-trust before the name binds; an unproven socket is refused and "
        "closed. Off by default, so an open hub is unchanged. Requires --identity-trust.",
    )
    hub.add_argument(
        "--capability-card-trust",
        default="",
        metavar="FILE",
        help="Separate Ed25519 trust bundle for advisory signed capability cards. "
        "Unsigned and failed cards remain visible with an explicit verification result.",
    )
    hub.add_argument(
        "--capability-card-history-db",
        default="",
        metavar="FILE",
        help="Optional owner-only SQLite database that preserves signed-card replay and "
        "downgrade history across hub restarts. Requires --capability-card-trust; it does "
        "not enable admission enforcement.",
    )
    hub.add_argument(
        "--capability-card-clock-skew-seconds",
        type=float,
        default=DEFAULT_CAPABILITY_CARD_CLOCK_SKEW_SECONDS,
        metavar="SECONDS",
        help="Clock skew tolerated when checking signed-card validity windows.",
    )
    hub.add_argument(
        "--capability-card-history-capacity",
        type=int,
        default=DEFAULT_CAPABILITY_CARD_HISTORY_CAPACITY,
        metavar="N",
        help="Bounded agent/key histories retained for card replay and downgrade checks.",
    )
    hub.add_argument(
        "--capability-card-history-retention-seconds",
        type=float,
        default=DEFAULT_CAPABILITY_CARD_HISTORY_RETENTION_SECONDS,
        metavar="SECONDS",
        help="How long expired signed-card history remains for replay detection.",
    )
    hub.add_argument(
        "--private-directed-messages",
        action="store_true",
        help="Route a directed message only to its recipients (and their -rx waiter "
        "sidecars) plus any identity granted the 'observe' ACL verb, instead of "
        "broadcasting it to every socket. Off by default. The relay log and journal still "
        "retain every message, so a feeds-backed dashboard and the federation follower keep "
        "full visibility.",
    )
    hub.add_argument(
        "--bridge-exposed",
        action="store_true",
        help="Declare that an A2A and/or MCP bridge is knowingly reachable alongside "
        "this hub (separate process or co-located). Off by default. When set, flood "
        "auto-enable (REV-SEC-06) treats the hub as bridge-exposed even on loopback, "
        "because bridge traffic can flood the hub without a connect token. Operators "
        "running a2a-serve/mcp against this hub should pass this flag.",
    )
    hub.add_argument(
        "--expect-multi-seat",
        action="store_true",
        help="Declare that more than one agent seat is expected on this hub. Off by "
        "default. Flood auto-enable also infers multi-seat from team-secure/secure, "
        "identity-trust, role-grants, private-directed-messages, and role/identity "
        "require flags; use this when those are absent but multi-seat is intended.",
    )
