# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unified `synapse` command-line entry point
"""Command-line entry point for the Synapse channel.

The ``synapse`` command exposes six subcommands:

* ``hub`` — run the coordination hub;
* ``worker`` — run a model worker that answers on the channel;
* ``team`` — launch a hub plus one or two local workers in one shot;
* ``send`` — connect, send one message, optionally wait for replies, and exit;
* ``listen`` — connect and stream channel messages until interrupted;
* ``relay`` — decode and print a lite relay log a hub mirrored to a file.

The send/listen helpers take an injectable agent factory so the dispatch and the
client flows are unit-testable without a live hub.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from synapse_channel import __version__
from synapse_channel.client import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.hub import (
    DEFAULT_HOST,
    DEFAULT_MAX_HISTORY,
    DEFAULT_PORT,
    DEFAULT_RELAY_MAX_LINES,
    SynapseHub,
)
from synapse_channel.launcher import run_team
from synapse_channel.llm_worker import (
    DEFAULT_OLLAMA_BASE_URL,
    SynapseLLMWorker,
)
from synapse_channel.persistence import EventStore
from synapse_channel.protocol import MessageType
from synapse_channel.ratelimit import RateLimiter
from synapse_channel.relay import decode_lite, load_offset, read_jsonl_since, save_offset

AgentFactory = Callable[..., SynapseAgent]


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
    limiter = (
        RateLimiter(rate_per_second=args.rate, burst=args.burst) if args.rate > 0 else None
    )
    hub = SynapseHub(
        journal=journal,
        rate_limiter=limiter,
        max_history=args.max_history,
        relay_log=args.relay_log,
        relay_max_lines=args.relay_max_lines,
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
    """Run a single on-channel model worker until interrupted."""
    worker = SynapseLLMWorker(
        name=args.name,
        uri=args.uri,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        max_context=args.max_context,
        reply_target_mode=args.reply_target_mode,
        min_reply_interval=args.min_reply_interval,
    )
    try:
        _run(worker.run())
    except KeyboardInterrupt:
        print(f"\n[{args.name}] stopped by user.")
    return 0


def _cmd_team(args: argparse.Namespace) -> int:
    """Launch a local hub plus one or two workers."""
    return run_team(
        port=args.port,
        no_workers=args.no_workers,
        fast_model=args.fast_model,
        reason_model=args.reason_model,
    )


async def _send(
    *,
    uri: str,
    name: str,
    target: str,
    message: str,
    wait_seconds: float,
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Send one chat message and optionally print replies for a window.

    Parameters
    ----------
    uri, name, target, message : str
        Hub URI, sender name, recipient, and message body.
    wait_seconds : float
        Seconds to keep listening for replies after sending (``0`` to skip).
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the hub could not be reached.
    """
    replies: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.CHAT and data.get("sender") != name:
            replies.append(data)

    agent = agent_factory(name, collect, uri=uri, verbose=False)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.chat(message, target=target)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
            for reply in replies:
                print(f"{reply.get('sender')}: {reply.get('payload')}")
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_send(args: argparse.Namespace) -> int:
    """Dispatch the ``send`` subcommand."""
    return asyncio.run(
        _send(
            uri=args.uri,
            name=args.name,
            target=args.target,
            message=args.message,
            wait_seconds=args.wait_seconds,
        )
    )


async def _listen(
    *, uri: str, name: str, agent_factory: AgentFactory = SynapseAgent
) -> int:
    """Stream chat and presence updates to stdout until the connection ends.

    Parameters
    ----------
    uri, name : str
        Hub URI and the listener's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.

    Returns
    -------
    int
        Always ``0`` once the connection closes.
    """

    async def show(data: dict[str, Any]) -> None:
        msg_type = data.get("type")
        if msg_type == MessageType.CHAT:
            print(f"{data.get('sender')}: {data.get('payload')}")
        elif msg_type == MessageType.PRESENCE_UPDATE:
            online = ", ".join(data.get("online_agents", []))
            print(f"[presence] {data.get('event')} -> online: {online}")

    agent = agent_factory(name, show, uri=uri, verbose=True)
    await agent.connect()
    return 0


def _cmd_listen(args: argparse.Namespace) -> int:
    """Dispatch the ``listen`` subcommand."""
    try:
        return asyncio.run(_listen(uri=args.uri, name=args.name))
    except KeyboardInterrupt:
        print(f"\n[{args.name}] stopped listening.")
        return 0


def _format_relay_line(message: dict[str, Any]) -> str:
    """Render one decoded relay event as a single human-readable line."""
    timestamp = message.get("timestamp", 0.0)
    return (
        f"[{float(timestamp):.3f}] "
        f"{message.get('sender', '?')} -> {message.get('target', 'all')} "
        f"({message.get('type', 'chat')}): {message.get('payload', '')}"
    )


def _cmd_relay(args: argparse.Namespace) -> int:
    """Decode and print a lite relay log a hub mirrored with ``--relay-log``.

    Reads the compact newline-delimited log, decodes each event back to a full
    envelope, and prints one line per event. With ``--cursor`` the read position
    is persisted between runs so repeated calls show only what was appended
    since; otherwise reading starts at the ``--since`` byte offset.
    """
    start = load_offset(args.cursor) if args.cursor else max(int(args.since), 0)
    events, cursor = read_jsonl_since(args.relay_log, start)
    for lite in events:
        print(_format_relay_line(decode_lite(lite)))
    if args.cursor:
        save_offset(args.cursor, cursor)
    return 0


# -- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(prog="synapse", description="Synapse multi-agent channel.")
    parser.add_argument("--version", action="version", version=f"synapse-channel {__version__}")
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
    hub.set_defaults(func=_cmd_hub)

    worker = sub.add_parser("worker", help="Run an on-channel model worker.")
    worker.add_argument("--name", default="FAST")
    worker.add_argument("--uri", default=DEFAULT_HUB_URI)
    worker.add_argument("--provider", choices=["openai", "ollama", "rule"], default="ollama")
    worker.add_argument("--model", default="llama3")
    worker.add_argument("--base-url", default=DEFAULT_OLLAMA_BASE_URL)
    worker.add_argument("--api-key-env", default="OPENAI_API_KEY")
    worker.add_argument("--max-context", type=int, default=8)
    worker.add_argument("--reply-target-mode", choices=["all", "sender"], default="all")
    worker.add_argument("--min-reply-interval", type=float, default=0.7)
    worker.set_defaults(func=_cmd_worker)

    team = sub.add_parser("team", help="Launch a hub plus local workers.")
    team.add_argument("--port", type=int, default=DEFAULT_PORT)
    team.add_argument("--no-workers", action="store_true")
    team.add_argument("--fast-model", default=None)
    team.add_argument("--reason-model", default=None)
    team.set_defaults(func=_cmd_team)

    send = sub.add_parser("send", help="Send one message and optionally await replies.")
    send.add_argument("--uri", default=DEFAULT_HUB_URI)
    send.add_argument("--name", default="USER")
    send.add_argument("--target", default="all")
    send.add_argument("--wait-seconds", type=float, default=2.0)
    send.add_argument("message")
    send.set_defaults(func=_cmd_send)

    listen = sub.add_parser("listen", help="Stream channel messages until interrupted.")
    listen.add_argument("--uri", default=DEFAULT_HUB_URI)
    listen.add_argument("--name", default="USER")
    listen.set_defaults(func=_cmd_listen)

    relay = sub.add_parser("relay", help="Decode and print a hub's lite relay log.")
    relay.add_argument("relay_log", help="Path to the lite relay log to read.")
    relay.add_argument(
        "--since", type=int, default=0, help="Byte offset to start reading from."
    )
    relay.add_argument(
        "--cursor",
        default=None,
        help="File holding a persisted read offset; resumes where the last run left off.",
    )
    relay.set_defaults(func=_cmd_relay)

    return parser


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
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
