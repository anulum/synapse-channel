# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unified `synapse` command-line entry point
"""Command-line entry point for the Synapse channel.

The ``synapse`` command exposes these subcommands:

* ``hub`` — run the coordination hub;
* ``worker`` — run a model worker that answers on the channel;
* ``team`` — launch a hub plus one or two local workers in one shot;
* ``send`` — connect, send one message, optionally wait for replies, and exit;
* ``wait`` — block until a message addressed to you arrives, then exit (a wake trigger);
* ``listen`` — connect and stream channel messages until interrupted;
* ``relay`` — decode and print a lite relay log a hub mirrored to a file;
* ``ingest`` — stream durable events from a hub event store since a sequence cursor (read-side);
* ``compact`` — bound the durable log: keep latest-N checkpoints per task, age out old findings;
* ``board`` — print the hub's shared task/progress blackboard;
* ``supervisor`` — run an LLM-free supervisor that re-offers stalled tasks;
* ``manifest`` — print the capability manifest of advertised agents;
* ``who`` — list the agents currently online, optionally for one project;
* ``state`` — print active claims and their checkpoints (a resume view);
* ``git-claim`` — claim a task scoped to the current git branch (branch resolved client-side);
* ``git-hook`` — install git hooks that auto-release branch-scoped claims on commit/merge;
* ``git-release`` — release branch-scoped claims whose paths were committed/merged (hook-invoked);
* ``conflicts`` — predict merge conflicts between branch-scoped claims on different branches;
* ``health`` — probe the hub and report reachability as the exit code;
* ``lock`` — hold a lease while running a command, to serialise it across agents;
* ``release`` — manually drop a claim you own (e.g. an ``--auto-release-on manual`` claim);
* ``task`` — declare and update the shared task plan from the command line;
* ``mcp`` — run a Model Context Protocol server over stdio, bridged to the hub.

This module keeps the hub-lifecycle commands (hub/worker/team/supervisor) and the
shared task-plan writes (task declare/update/progress); the messaging
(send/wait/listen), read-only query (who/state/board/manifest/health), git,
locking, mcp, and file/event subcommands live in their own ``cli_*`` modules and
register through :func:`build_parser`. The task writes reuse :func:`_query_hub`
from :mod:`synapse_channel.cli_queries`, and every helper takes an injectable
agent factory so the dispatch and the client flows are unit-testable without a
live hub.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel import __version__
from synapse_channel.cli_git import add_parsers as add_git_parsers
from synapse_channel.cli_locking import add_parsers as add_locking_parsers
from synapse_channel.cli_mcp import add_parsers as add_mcp_parsers
from synapse_channel.cli_messaging import add_parsers as add_messaging_parsers
from synapse_channel.cli_queries import _query_hub
from synapse_channel.cli_queries import add_parsers as add_query_parsers
from synapse_channel.cli_streams import add_parsers as add_stream_parsers
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.client.launcher import run_team
from synapse_channel.client.llm_worker import (
    DEFAULT_OLLAMA_BASE_URL,
    SynapseLLMWorker,
)
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
from synapse_channel.core.protocol import (
    MessageType,
)
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.update_check import update_notice

AgentFactory = Callable[..., SynapseAgent]


class _VersionAction(argparse.Action):
    """Print the version and a best-effort upgrade notice, then exit.

    Behaves like argparse's built-in ``version`` action (prints and raises
    ``SystemExit``) but appends a one-line PyPI upgrade notice on stderr when a newer
    release exists. The notice is best-effort and silenced by ``SYNAPSE_NO_UPDATE_CHECK``.
    """

    def __init__(self, option_strings: list[str], dest: str, **kwargs: Any) -> None:
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("help", "show the version (and any available upgrade) and exit")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        print(f"synapse-channel {__version__}")
        notice = update_notice()
        if notice:
            print(notice, file=sys.stderr)
        parser.exit()


def _run(coro: Coroutine[Any, Any, None]) -> None:
    """Run a coroutine on a fresh event loop (indirection eases testing)."""
    asyncio.run(coro)


# -- command handlers ---------------------------------------------------------


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


async def _task_action(
    *,
    uri: str,
    name: str,
    token: str | None,
    confirm_type: str,
    send: Callable[[SynapseAgent], Awaitable[None]],
    render: Callable[[dict[str, Any]], str],
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Connect, run one blackboard write, print the hub's confirmation, and exit.

    Parameters
    ----------
    uri, name : str
        Hub URI and the author's display name.
    token : str or None
        Shared-secret token for a secured hub.
    confirm_type : str
        Message type the hub broadcasts to confirm the write.
    send : Callable
        Coroutine that performs the write on the connected agent.
    render : Callable
        Formats the confirmation message into a line for stdout.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.

    Returns
    -------
    int
        ``0`` once the confirmation is printed, ``1`` when the hub was unreachable.
    """
    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=confirm_type,
        request=send,
        render=lambda data: print(render(data)),
        attempts=60,
    )


def _cmd_task_declare(
    args: argparse.Namespace, *, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Declare a task on the shared blackboard."""
    deps = tuple(args.depends_on) if args.depends_on else ()

    async def send(agent: SynapseAgent) -> None:
        await agent.post_task(args.task_id, title=args.title, depends_on=deps)

    def render(msg: dict[str, Any]) -> str:
        task = msg.get("task", {})
        deps_txt = ", ".join(task.get("depends_on", [])) or "none"
        return f"declared {task.get('task_id')} — {task.get('title')} (deps: {deps_txt})"

    return asyncio.run(
        _task_action(
            uri=args.uri,
            name=args.name,
            token=args.token,
            confirm_type=MessageType.LEDGER_TASK_POSTED,
            send=send,
            render=render,
            agent_factory=agent_factory,
        )
    )


def _cmd_task_update(
    args: argparse.Namespace, *, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Update a blackboard task's status or suggested owner."""

    async def send(agent: SynapseAgent) -> None:
        await agent.update_ledger_task(
            args.task_id, status=args.status, suggested_owner=args.suggested_owner
        )

    def render(msg: dict[str, Any]) -> str:
        task = msg.get("task", {})
        return f"updated {task.get('task_id')} -> status={task.get('status')}"

    return asyncio.run(
        _task_action(
            uri=args.uri,
            name=args.name,
            token=args.token,
            confirm_type=MessageType.LEDGER_TASK_UPDATED,
            send=send,
            render=render,
            agent_factory=agent_factory,
        )
    )


def _cmd_task_progress(
    args: argparse.Namespace, *, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Post a progress note against a task on the blackboard."""

    async def send(agent: SynapseAgent) -> None:
        await agent.post_progress(args.task_id, args.text, kind=args.kind)

    def render(msg: dict[str, Any]) -> str:
        note = msg.get("progress", {})
        task_id = note.get("task_id") or args.task_id
        return f"posted {note.get('kind', args.kind)} on {task_id}: {note.get('text', args.text)}"

    return asyncio.run(
        _task_action(
            uri=args.uri,
            name=args.name,
            token=args.token,
            confirm_type=MessageType.LEDGER_PROGRESS_POSTED,
            send=send,
            render=render,
            agent_factory=agent_factory,
        )
    )


def _cmd_task_help(args: argparse.Namespace) -> int:
    """Print usage when ``synapse task`` is run without an action."""
    del args
    print("Usage: synapse task {declare|update|progress} <task_id> ... (see synapse task -h)")
    return 1


# -- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(prog="synapse", description="Synapse multi-agent channel.")
    parser.add_argument("--version", action=_VersionAction)
    sub = parser.add_subparsers(dest="command")

    hub = sub.add_parser("hub", help="Run the coordination hub.")
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

    worker = sub.add_parser("worker", help="Run an on-channel model worker.")
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

    team = sub.add_parser("team", help="Launch a hub plus local workers.")
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

    add_messaging_parsers(sub)

    add_query_parsers(sub)

    add_mcp_parsers(sub)

    add_git_parsers(sub)

    add_locking_parsers(sub)

    add_stream_parsers(sub)

    supervisor = sub.add_parser(
        "supervisor", help="Run an LLM-free supervisor that re-offers stalled tasks."
    )
    supervisor.add_argument("--uri", default=DEFAULT_HUB_URI)
    supervisor.add_argument("--name", default="SUPERVISOR")
    supervisor.add_argument("--idle-seconds", type=float, default=DEFAULT_IDLE_SECONDS)
    supervisor.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    supervisor.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    supervisor.set_defaults(func=_cmd_supervisor)

    task = sub.add_parser("task", help="Declare and update the shared task plan.")
    task.set_defaults(func=_cmd_task_help)
    task_sub = task.add_subparsers(dest="task_command")

    def _add_task_common(parser_: argparse.ArgumentParser) -> None:
        parser_.add_argument("--uri", default=DEFAULT_HUB_URI)
        parser_.add_argument("--name", default="USER")
        parser_.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")

    declare = task_sub.add_parser("declare", help="Declare a task on the blackboard.")
    declare.add_argument("task_id")
    declare.add_argument("--title", default="")
    declare.add_argument(
        "--depends-on",
        action="append",
        default=None,
        help="Task id this one depends on (repeatable).",
    )
    _add_task_common(declare)
    declare.set_defaults(func=_cmd_task_declare)

    update = task_sub.add_parser("update", help="Update a task's status or suggested owner.")
    update.add_argument("task_id")
    update.add_argument("--status", default=None, help="New status, e.g. done.")
    update.add_argument("--suggested-owner", default=None)
    _add_task_common(update)
    update.set_defaults(func=_cmd_task_update)

    progress = task_sub.add_parser("progress", help="Post a progress note on a task.")
    progress.add_argument("task_id")
    progress.add_argument("text")
    progress.add_argument("--kind", default="note")
    _add_task_common(progress)
    progress.set_defaults(func=_cmd_task_progress)

    # Give every command that takes --token a --token-file companion, so the secret
    # can come from a file instead of argv (which is visible to anyone running `ps`).
    for subparser in sub.choices.values():
        if any("--token" in action.option_strings for action in subparser._actions):
            subparser.add_argument(
                "--token-file",
                default=None,
                help="Read the shared-secret token from this file instead of --token.",
            )

    return parser


#: Environment variable read as a fallback source for the hub shared-secret token.
TOKEN_ENV = "SYNAPSE_TOKEN"


def _resolve_token(args: argparse.Namespace) -> str | None:
    """Resolve the hub token from ``--token``, then ``--token-file``, then the env var.

    Precedence is ``--token`` (an explicit override) → ``--token-file`` → the
    ``SYNAPSE_TOKEN`` environment variable. Prefer ``--token-file`` or the
    environment variable for a real secret: a ``--token`` value is visible in the
    process list. (This describes which source is *used*, not which is more secure
    — a value passed as ``--token`` is exposed regardless of what wins.)

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments; uses ``token`` and the optional ``token_file``.

    Returns
    -------
    str or None
        The resolved token, or ``None`` when no source supplies one.
    """
    if args.token:
        return str(args.token)
    token_file = getattr(args, "token_file", None)
    if token_file:
        return Path(token_file).read_text(encoding="utf-8").strip()
    return os.environ.get(TOKEN_ENV) or None


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the selected subcommand.

    Parameters
    ----------
    argv : list[str] or None, optional
        Argument vector; defaults to ``sys.argv[1:]`` when ``None``.

    Returns
    -------
    int
        The selected command's exit code, or ``1`` when no command was given.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    if hasattr(args, "token"):
        args.token = _resolve_token(args)
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
