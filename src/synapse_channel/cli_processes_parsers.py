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

from synapse_channel.cli_processes_hub import _cmd_hub
from synapse_channel.cli_processes_supervisor import _cmd_supervisor
from synapse_channel.cli_processes_team import _cmd_team
from synapse_channel.cli_processes_worker import _cmd_worker
from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.client.llm_worker import DEFAULT_OLLAMA_BASE_URL
from synapse_channel.client.supervisor import (
    DEFAULT_HISTORY_MULTIPLIER,
    DEFAULT_IDLE_SECONDS,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MIN_HISTORY_SAMPLES,
    DEFAULT_MIN_PREDICTIVE_IDLE_SECONDS,
)
from synapse_channel.core.hub import (
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_COMPACT_HINT_THRESHOLD,
    DEFAULT_HOST,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_MSG_BYTES,
    DEFAULT_PORT,
    DEFAULT_RELAY_MAX_LINES,
    DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
    DEFAULT_TAKEOVER_COOLDOWN,
)
from synapse_channel.core.logging_setup import (
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOG_LEVEL,
    LOG_FORMATS,
    LOG_LEVELS,
)
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import MAX_CLAIMS_PER_AGENT, MAX_OFFERS_PER_AGENT


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
        "--rate",
        type=float,
        default=0.0,
        help="Per-agent sustained message rate (msgs/sec); 0 disables rate limiting.",
    )
    hub.add_argument(
        "--burst", type=float, default=20.0, help="Per-agent burst allowance for --rate."
    )
    hub.add_argument(
        "--host-rate",
        type=float,
        default=0.0,
        help="Per-host sustained frame rate (frames/sec, heartbeats included); 0 disables it.",
    )
    hub.add_argument(
        "--host-burst", type=float, default=40.0, help="Per-host burst allowance for --host-rate."
    )
    hub.add_argument(
        "--max-history",
        type=int,
        default=DEFAULT_MAX_HISTORY,
        help="Maximum chat messages retained in memory.",
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
        default=0,
        help="Maximum simultaneous sockets admitted from one remote host; 0 disables "
        "the per-host connection-count cap.",
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
        "--shutdown-close-timeout",
        type=float,
        default=DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
        help="Seconds active WebSocket close handshakes may delay hub shutdown.",
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
        "--metrics-query-token-ok",
        action="store_true",
        help="Also accept the metrics token as a ?token= query parameter (off by "
        "default; a query token can leak into logs, history, and proxy records).",
    )
    hub.add_argument(
        "--insecure-off-loopback",
        action="store_true",
        help="Bind a non-loopback host even without a token (and metrics token); by "
        "default such an exposed bind is refused rather than only warned about.",
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
    worker.add_argument("--uri", default=DEFAULT_HUB_URI)
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
    supervisor.add_argument("--uri", default=DEFAULT_HUB_URI)
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
