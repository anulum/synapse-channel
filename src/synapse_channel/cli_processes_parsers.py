# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI parser registration
"""Parser registration for long-running process commands."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from synapse_channel.cli_processes_hub import _cmd_hub
from synapse_channel.cli_processes_security_args import add_hub_security_arguments
from synapse_channel.cli_processes_supervisor import _cmd_supervisor
from synapse_channel.cli_processes_team import _cmd_team
from synapse_channel.cli_processes_worker import _cmd_worker
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.client.llm_worker import DEFAULT_OLLAMA_BASE_URL
from synapse_channel.client.supervisor import (
    DEFAULT_HISTORY_MULTIPLIER,
    DEFAULT_IDLE_SECONDS,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MIN_HISTORY_SAMPLES,
    DEFAULT_MIN_PREDICTIVE_IDLE_SECONDS,
)
from synapse_channel.core.agent_liveness import (
    DEFAULT_RECIPIENT_LIVENESS_WINDOW,
    DEFAULT_WAITER_LIVENESS_WINDOW,
    DEFAULT_WARN_STALE_RECIPIENTS,
)
from synapse_channel.core.capability_card_signing import (
    DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS,
)
from synapse_channel.core.hub import (
    DEFAULT_COMPACT_HINT_THRESHOLD,
    DEFAULT_HOST,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_CONNECTIONS_PER_HOST,
    DEFAULT_MAX_FINDINGS_PER_AGENT,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_MSG_BYTES,
    DEFAULT_PORT,
    DEFAULT_RELAY_MAX_LINES,
    DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
    DEFAULT_TAKEOVER_COOLDOWN,
)
from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    DEFAULT_MAX_PROGRESS_PER_TASK,
)
from synapse_channel.core.logging_setup import (
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOG_LEVEL,
    LOG_FORMATS,
    LOG_LEVELS,
)
from synapse_channel.core.name_ownership import DEFAULT_LEASE_OFFLINE_TTL
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import MAX_CLAIMS_PER_AGENT, MAX_OFFERS_PER_AGENT


def _finite_limit(value: str) -> float:
    """Parse a rate or burst limit as a finite, non-negative float for argparse.

    ``nan`` would silently disable the limiter downstream (``nan > 0`` is false)
    while looking configured, and ``inf`` would configure an unbounded token
    bucket, so both are rejected at the argument boundary for every hub run —
    not only under a hardening preset.
    """
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError(f"{value!r} must be a finite non-negative number")
    return parsed


def _non_negative_int(value: str) -> int:
    """Parse an integer limit whose zero value disables the policy."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"{value!r} must be a non-negative integer")
    return parsed


def _add_logging_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--log-format`` / ``--log-level`` options to a daemon parser."""
    parser.add_argument(
        "--log-format",
        choices=list(LOG_FORMATS),
        default=DEFAULT_LOG_FORMAT,
        help="Log output format: human-readable text or line-delimited JSON.",
    )
    parser.add_argument(
        "--log-level",
        choices=list(LOG_LEVELS),
        default=DEFAULT_LOG_LEVEL,
        help="Minimum level emitted to the log stream.",
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``hub``, ``worker``, ``team``, and ``supervisor`` subparsers."""
    hub = subparsers.add_parser("hub", help="Run the coordination hub.")
    _add_logging_args(hub)
    hub.add_argument("--host", default=DEFAULT_HOST)
    hub.add_argument("--port", type=int, default=DEFAULT_PORT)
    hub.add_argument(
        "--db",
        default=None,
        help="Path to a durable event-log database; enables crash-safe persistence.",
    )
    hub.add_argument(
        "--db-key-file",
        default=None,
        help=(
            "Owner-only 32-byte key file for SQLCipher page encryption of --db "
            "(requires synapse-channel[sqlcipher]; generate with synapse encrypt-key generate)."
        ),
    )
    hub.add_argument(
        "--rate",
        type=_finite_limit,
        default=0.0,
        help="Per-agent sustained message rate (msgs/sec); 0 disables rate limiting.",
    )
    hub.add_argument(
        "--burst",
        type=_finite_limit,
        default=20.0,
        help="Per-agent burst allowance for --rate.",
    )
    hub.add_argument(
        "--host-rate",
        type=_finite_limit,
        default=0.0,
        help="Per-host sustained frame rate (frames/sec, heartbeats included); 0 disables it.",
    )
    hub.add_argument(
        "--host-burst",
        type=_finite_limit,
        default=40.0,
        help="Per-host burst allowance for --host-rate.",
    )
    hub.add_argument(
        "--durable-ingress-events",
        type=_non_negative_int,
        default=0,
        help=(
            "Max accepted chat events per principal inside --durable-ingress-window; "
            "0 disables durable-ingress quotas (default)."
        ),
    )
    hub.add_argument(
        "--durable-ingress-bytes",
        type=_non_negative_int,
        default=0,
        help=(
            "Max accepted serialized chat-frame bytes per principal inside the window; "
            "0 disables unless --durable-ingress-events is set (then defaults to 1 MiB)."
        ),
    )
    hub.add_argument(
        "--durable-ingress-window",
        type=_finite_limit,
        default=60.0,
        help="Sliding window seconds for durable-ingress event/frame-byte quotas.",
    )
    hub.add_argument(
        "--max-history",
        type=int,
        default=DEFAULT_MAX_HISTORY,
        help="Maximum chat messages retained in memory.",
    )
    hub.add_argument(
        "--max-progress",
        type=int,
        default=DEFAULT_MAX_PROGRESS,
        help="Maximum blackboard progress notes retained in memory.",
    )
    hub.add_argument(
        "--max-progress-per-author",
        type=int,
        default=DEFAULT_MAX_PROGRESS_PER_AUTHOR,
        help="Maximum blackboard progress notes retained per author.",
    )
    hub.add_argument(
        "--max-progress-per-task",
        type=int,
        default=DEFAULT_MAX_PROGRESS_PER_TASK,
        help="Maximum blackboard progress notes retained per task id.",
    )
    hub.add_argument(
        "--board-task-cap",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Bound the tasks served per board snapshot: live tasks kept ahead of "
            "terminal ones, newest updated first, and the reply carries "
            "total_tasks/truncated so consumers see the bound. Default serves the "
            "full board; set it when a long-running fleet's board outgrows a "
            "websocket frame."
        ),
    )
    hub.add_argument(
        "--max-findings-per-agent",
        type=int,
        default=DEFAULT_MAX_FINDINGS_PER_AGENT,
        help="Maximum durable findings one agent may admit before private rejection.",
    )
    hub.add_argument(
        "--relay-log",
        default=None,
        help="Mirror every broadcast to this lite NDJSON log for file-based observers.",
    )
    hub.add_argument(
        "--relay-max-lines",
        type=int,
        default=DEFAULT_RELAY_MAX_LINES,
        help="Upper bound on the relay log before it is trimmed.",
    )
    hub.add_argument(
        "--max-clients",
        type=int,
        default=DEFAULT_MAX_CLIENTS,
        help="Maximum simultaneous connections before further connects are refused.",
    )
    hub.add_argument(
        "--max-unauth-clients",
        type=int,
        default=None,
        help="On a secured hub, the most sockets allowed mid-authentication at once "
        "(default: same as --max-clients), bounding an authentication-stall burst.",
    )
    hub.add_argument(
        "--max-connections-per-host",
        type=int,
        default=DEFAULT_MAX_CONNECTIONS_PER_HOST,
        help=(
            "Maximum simultaneous sockets admitted from one remote host "
            f"(default {DEFAULT_MAX_CONNECTIONS_PER_HOST}); 0 disables the "
            "per-host connection-count cap."
        ),
    )
    hub.add_argument(
        "--max-msg-kb",
        type=int,
        default=DEFAULT_MAX_MSG_BYTES // 1024,
        help="Largest accepted inbound message in KiB; a larger frame is rejected.",
    )
    hub.add_argument(
        "--max-claims-per-agent",
        type=int,
        default=MAX_CLAIMS_PER_AGENT,
        help="Most live claims one agent may hold before further claims are refused.",
    )
    hub.add_argument(
        "--max-offers-per-agent",
        type=int,
        default=MAX_OFFERS_PER_AGENT,
        help="Most live resource offers one agent may register before new offers are refused.",
    )
    hub.add_argument(
        "--max-paths-per-claim",
        type=int,
        default=MAX_DECLARED_PATHS,
        help="Most distinct paths one claim may declare before its scope widens to the worktree.",
    )
    hub.add_argument(
        "--compact-hint-threshold",
        type=int,
        default=DEFAULT_COMPACT_HINT_THRESHOLD,
        help="Event-log record count past which the hub hints at running `synapse compact`.",
    )
    hub.add_argument(
        "--takeover-cooldown",
        type=float,
        default=DEFAULT_TAKEOVER_COOLDOWN,
        help="Seconds a name is protected from a second takeover, to blunt an eviction storm.",
    )
    hub.add_argument(
        "--identity-pins",
        default=str(Path.home() / "synapse" / "identity-pins.json"),
        help="JSON file persisting trust-on-first-use name-to-key identity pins; "
        "pass an empty string to keep pins in memory only.",
    )
    hub.add_argument(
        "--lease-offline-ttl",
        type=float,
        default=DEFAULT_LEASE_OFFLINE_TTL,
        help="Seconds a name ownership lease outlives its holder disconnect before the "
        "name returns to first-come-first-owned; 0 ends the lease at disconnect.",
    )
    hub.add_argument(
        "--shutdown-close-timeout",
        type=float,
        default=DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
        help="Seconds active WebSocket close handshakes may delay hub shutdown.",
    )
    add_hub_security_arguments(hub)
    hub.add_argument(
        "--warn-stale-recipients",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_WARN_STALE_RECIPIENTS,
        help="Warn a sender when a directed recipient is present but not proven wake-capable — "
        "no armed -rx waiter sidecar and no genuine reaction within "
        "--recipient-liveness-window seconds. Such a stale-only match is dead-lettered and "
        "returns no_live_recipient instead of a positive receipt. Enabled by default; use "
        "--no-warn-stale-recipients for the legacy socket-presence compatibility behavior.",
    )
    hub.add_argument(
        "--recipient-liveness-window",
        type=float,
        default=DEFAULT_RECIPIENT_LIVENESS_WINDOW,
        metavar="SECONDS",
        help="How long after its last genuine reaction a recipient stays judged live for the "
        "warning and delivery gate. A just-connected or briefly quiet agent is inside the "
        f"window and never flagged. Defaults to {DEFAULT_RECIPIENT_LIVENESS_WINDOW:g}s.",
    )
    hub.add_argument(
        "--waiter-liveness-window",
        type=float,
        default=DEFAULT_WAITER_LIVENESS_WINDOW,
        metavar="SECONDS",
        help="How long a waiter's -rx sidecar may go silent (no keepalive) before it stops "
        "counting as a live waiter for the warning and delivery gate, so a hung waiter no "
        f"longer vouches for its agent. Defaults to {DEFAULT_WAITER_LIVENESS_WINDOW:g}s.",
    )
    hub.add_argument(
        "--federation-store",
        default="",
        metavar="FILE",
        help="Compose operator-confirmed peer domains from this federation store "
        "(written by `synapse federation import`) into the live frame authorisation. "
        "Off by default; a cross-domain frame is honoured only with --require-message-auth, "
        "which binds its authority.",
    )
    hub.add_argument(
        "--federation-offer",
        default="",
        metavar="FILE",
        help="Serve this domain's own federation-bundle material (a peer-bundle JSON, "
        "authored with `synapse federation offer`) to a peer operator's "
        "`synapse federation fetch`. Off by default; the fetched material stays "
        "untrusted until the fetching operator compares fingerprints out-of-band "
        "and imports it explicitly.",
    )
    hub.add_argument(
        "--federation-observe-only",
        action="store_true",
        help="Declare the federation store is loaded for diagnostics and deny-closed "
        "refusal only, never to honour a cross-domain frame. Required to start when the "
        "store grants cross-domain scope without --require-message-auth; contradicts "
        "--require-message-auth.",
    )
    hub.add_argument(
        "--hub-id",
        default=None,
        metavar="ID",
        help="Stable hub id (default: a generated syn-<hex>). Required by "
        "--namespace-owner, whose ownership map compares owners against this id.",
    )
    hub.add_argument(
        "--namespace-owner",
        action="append",
        default=[],
        metavar="NS=HUB_ID",
        help="Declare the single authoritative owning hub of a namespace (repeatable). "
        "Deny-by-default claim routing: a namespace absent from the map is ungoverned "
        "and grants nothing; a namespace owned by another hub is refused with the "
        "owner named. Requires --hub-id.",
    )
    hub.add_argument(
        "--multihub-watch",
        action="append",
        default=[],
        metavar="PEER=URI",
        help="Poll this named peer hub's event log and feed the observed asserting-owner "
        "view into partition detection (repeatable). Naming a peer here IS the operator "
        "confirmation for an always-on outbound connection. Requires --namespace-owner; "
        "a failed poll keeps the last successful observation (refusing side).",
    )
    hub.add_argument(
        "--multihub-watch-interval",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Seconds between watch poll rounds (floor 1.0).",
    )
    hub.add_argument(
        "--multihub-watch-token",
        default=None,
        metavar="TOKEN",
        help="Authentication token sent to secured watch peers.",
    )
    hub.add_argument(
        "--multihub-watch-pin",
        action="append",
        default=[],
        metavar="PEER=sha256:HEX",
        help="Accept the named watch peer's wss:// certificate only by this SHA-256 pin "
        "(repeatable; self-signed or private-CA peers, no CA needed). The peer must "
        "also be named by --multihub-watch.",
    )
    hub.add_argument(
        "--insecure-off-loopback",
        action="store_true",
        help="Bind a non-loopback host even without a token (and metrics token); by "
        "default such an exposed bind is refused rather than only warned about.",
    )
    hub.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="Admit browser WebSocket handshakes from this exact concrete Origin "
        "(scheme://host[:port]); repeat to allow several. Opaque 'null' and wildcards "
        "are refused. Without this list every Origin-bearing browser handshake is "
        "rejected; native clients without Origin still require a trusted Host.",
    )
    hub.add_argument(
        "--advertised-host",
        default=None,
        metavar="HOST[:PORT]",
        help="Trusted Host authority clients use when the bind is not loopback "
        "(or behind a reverse proxy). Required for fail-closed Host checks on "
        "0.0.0.0/:: binds; never invents a wildcard trust.",
    )
    hub.set_defaults(func=_cmd_hub)

    worker = subparsers.add_parser("worker", help="Run an on-channel model worker.")
    _add_logging_args(worker)
    worker.add_argument("--name", default="FAST")
    worker.add_argument(
        "--prefix",
        default="",
        help="Namespace prepended to --name to form the worker's identity, e.g. "
        "'remanentia/' so the same role runs per project without a name clash.",
    )
    worker.add_argument("--uri", default=default_hub_uri())
    worker.add_argument(
        "--provider", choices=["openai", "ollama", "rule", "tiered"], default="ollama"
    )
    worker.add_argument("--model", default="llama3")
    worker.add_argument(
        "--heavy-model", default="", help="Model for the heavy tier when --provider tiered."
    )
    worker.add_argument("--base-url", default=DEFAULT_OLLAMA_BASE_URL)
    worker.add_argument("--api-key-env", default="OPENAI_API_KEY")
    worker.add_argument("--max-context", type=int, default=8)
    worker.add_argument("--reply-target-mode", choices=["all", "sender"], default="all")
    worker.add_argument("--min-reply-interval", type=float, default=0.7)
    worker.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    worker.add_argument(
        "--task-class",
        action="append",
        default=None,
        help="Routing class to advertise (repeatable); defaults to 'chat'.",
    )
    worker.add_argument(
        "--capability-card-key",
        default=None,
        metavar="FILE",
        help="Owner-only Ed25519 PEM used to sign this worker's capability card.",
    )
    worker.add_argument(
        "--capability-card-key-id",
        default="",
        metavar="ID",
        help="Public id of --capability-card-key in the hub card-trust bundle.",
    )
    worker.add_argument(
        "--capability-card-project",
        default="",
        metavar="PROJECT",
        help="Optional assertion of the worker name prefix before '/'; must match it.",
    )
    worker.add_argument(
        "--capability-card-lifetime-seconds",
        type=float,
        default=DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS,
        metavar="SECONDS",
        help="Signed-card lifetime; defaults to the live capability TTL.",
    )
    worker.set_defaults(func=_cmd_worker)

    team = subparsers.add_parser("team", help="Launch a hub plus local workers.")
    team.add_argument("--port", type=int, default=DEFAULT_PORT)
    team.add_argument("--no-workers", action="store_true")
    team.add_argument("--fast-model", default=None)
    team.add_argument("--reason-model", default=None)
    team.add_argument(
        "--prefix",
        default="",
        help="Namespace prepended to every worker name (e.g. 'remanentia/'), so a "
        "team can run per project without clashing with another project's roster.",
    )
    team.set_defaults(func=_cmd_team)

    supervisor = subparsers.add_parser(
        "supervisor", help="Run an LLM-free supervisor that re-offers stalled tasks."
    )
    supervisor.add_argument("--uri", default=default_hub_uri())
    supervisor.add_argument("--name", default="SUPERVISOR")
    supervisor.add_argument("--idle-seconds", type=float, default=DEFAULT_IDLE_SECONDS)
    supervisor.add_argument(
        "--no-predictive-stall",
        action="store_false",
        dest="predictive_stall",
        help="Disable completed-task history when deciding whether in-progress work stalled.",
    )
    supervisor.add_argument(
        "--history-multiplier",
        type=float,
        default=DEFAULT_HISTORY_MULTIPLIER,
        help="Multiplier applied to the median historical activity gap.",
    )
    supervisor.add_argument(
        "--min-history-samples",
        type=int,
        default=DEFAULT_MIN_HISTORY_SAMPLES,
        help="Minimum historical activity gaps required before predictive stall detection is used.",
    )
    supervisor.add_argument(
        "--min-predictive-idle-seconds",
        type=float,
        default=DEFAULT_MIN_PREDICTIVE_IDLE_SECONDS,
        help="Floor below which predictive stall detection never re-offers a task.",
    )
    supervisor.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    supervisor.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    supervisor.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    supervisor.set_defaults(func=_cmd_supervisor)
