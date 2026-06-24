# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — long-running process CLI commands (hub, worker, team, supervisor)
"""The long-running process ``synapse`` subcommands.

These commands start a process that runs until interrupted, rather than issuing
one request and exiting: ``hub`` runs the coordination hub, ``worker`` runs an
on-channel model worker, ``team`` launches a hub plus local workers in one shot,
and ``supervisor`` runs an LLM-free supervisor that re-offers stalled tasks. They
are grouped here, apart from the one-shot messaging/query/task commands, so each
module stays one responsibility; :func:`add_parsers` registers their subparsers
on the top-level CLI. :func:`_run` is the thin event-loop entry point the
blocking handlers share (the indirection eases testing).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Coroutine
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.client.launcher import run_team
from synapse_channel.client.llm_worker import DEFAULT_OLLAMA_BASE_URL, SynapseLLMWorker
from synapse_channel.client.supervisor import (
    DEFAULT_IDLE_SECONDS,
    DEFAULT_INTERVAL_SECONDS,
    SupervisorWorker,
)
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import (
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_HOST,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_MSG_BYTES,
    DEFAULT_PORT,
    DEFAULT_RELAY_MAX_LINES,
    SynapseHub,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.ratelimit import RateLimiter


def _run(coro: Coroutine[Any, Any, None]) -> None:
    """Run a coroutine on a fresh event loop (indirection eases testing)."""
    asyncio.run(coro)


def _cmd_hub(args: argparse.Namespace) -> int:
    """Run the coordination hub until interrupted.

    With ``--db`` the hub persists authoritative state to a durable event log and
    resumes from it on restart; without it the hub is purely in-memory.
    """
    journal = EventStore(args.db) if args.db else None
    limiter = RateLimiter(rate_per_second=args.rate, burst=args.burst) if args.rate > 0 else None
    authenticator = TokenAuthenticator([args.token]) if args.token else None
    hub = SynapseHub(
        journal=journal,
        rate_limiter=limiter,
        max_history=args.max_history,
        relay_log=args.relay_log,
        relay_max_lines=args.relay_max_lines,
        authenticator=authenticator,
        max_clients=args.max_clients,
        max_msg_bytes=args.max_msg_kb * 1024,
        enable_metrics=args.metrics,
        auth_timeout=args.auth_timeout,
        metrics_token=args.metrics_token,
    )
    try:
        _run(hub.serve(host=args.host, port=args.port))
    except KeyboardInterrupt:
        print("\nHub stopped by user.")
    finally:
        if journal is not None:
            journal.close()
    return 0


def _cmd_worker(args: argparse.Namespace) -> int:
    """Run a single on-channel model worker until interrupted.

    ``--prefix`` is prepended to ``--name`` to form the registered identity, so
    the same role can run under several projects without a name clash on the hub.
    """
    name = f"{args.prefix}{args.name}"
    worker = SynapseLLMWorker(
        name=name,
        uri=args.uri,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        max_context=args.max_context,
        reply_target_mode=args.reply_target_mode,
        min_reply_interval=args.min_reply_interval,
        token=args.token,
        task_classes=tuple(args.task_class) if args.task_class else ("chat",),
        heavy_model=args.heavy_model,
    )
    try:
        _run(worker.run())
    except KeyboardInterrupt:
        print(f"\n[{name}] stopped by user.")
    return 0


def _cmd_supervisor(args: argparse.Namespace) -> int:
    """Run an LLM-free supervisor that re-offers stalled tasks until interrupted."""
    supervisor = SupervisorWorker(
        name=args.name,
        uri=args.uri,
        idle_seconds=args.idle_seconds,
        interval=args.interval,
        token=args.token,
    )
    try:
        _run(supervisor.run())
    except KeyboardInterrupt:
        print(f"\n[{args.name}] supervisor stopped by user.")
    return 0


def _cmd_team(args: argparse.Namespace) -> int:
    """Launch a local hub plus one or two workers."""
    return run_team(
        port=args.port,
        no_workers=args.no_workers,
        fast_model=args.fast_model,
        reason_model=args.reason_model,
        prefix=args.prefix,
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``hub``, ``worker``, ``team``, and ``supervisor`` subparsers."""
    hub = subparsers.add_parser("hub", help="Run the coordination hub.")
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
        "--max-msg-kb",
        type=int,
        default=DEFAULT_MAX_MSG_BYTES // 1024,
        help="Largest accepted inbound message in KiB; a larger frame is rejected.",
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
        help="Require this token (Authorization: Bearer, or ?token=) for /metrics and "
        "/health, so an exposed endpoint does not leak metadata (off by default).",
    )
    hub.set_defaults(func=_cmd_hub)

    worker = subparsers.add_parser("worker", help="Run an on-channel model worker.")
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
    supervisor.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    supervisor.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    supervisor.set_defaults(func=_cmd_supervisor)
