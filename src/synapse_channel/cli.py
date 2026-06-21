# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unified `synapse` command-line entry point
"""Command-line entry point for the Synapse channel.

The ``synapse`` command exposes fourteen subcommands:

* ``hub`` — run the coordination hub;
* ``worker`` — run a model worker that answers on the channel;
* ``team`` — launch a hub plus one or two local workers in one shot;
* ``send`` — connect, send one message, optionally wait for replies, and exit;
* ``wait`` — block until a message addressed to you arrives, then exit (a wake trigger);
* ``listen`` — connect and stream channel messages until interrupted;
* ``relay`` — decode and print a lite relay log a hub mirrored to a file;
* ``board`` — print the hub's shared task/progress blackboard;
* ``supervisor`` — run an LLM-free supervisor that re-offers stalled tasks;
* ``manifest`` — print the capability manifest of advertised agents;
* ``who`` — list the agents currently online, optionally for one project;
* ``state`` — print active claims and their checkpoints (a resume view);
* ``lock`` — hold a lease while running a command, to serialise it across agents;
* ``task`` — declare and update the shared task plan from the command line.

The send/listen helpers take an injectable agent factory so the dispatch and the
client flows are unit-testable without a live hub.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from synapse_channel import __version__
from synapse_channel.auth import TokenAuthenticator
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
from synapse_channel.protocol import MessageType, addresses_project, is_directed, is_recipient
from synapse_channel.ratelimit import RateLimiter
from synapse_channel.relay import decode_lite, load_offset, read_jsonl_since, save_offset
from synapse_channel.supervisor import (
    DEFAULT_IDLE_SECONDS,
    DEFAULT_INTERVAL_SECONDS,
    SupervisorWorker,
)

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
    limiter = RateLimiter(rate_per_second=args.rate, burst=args.burst) if args.rate > 0 else None
    authenticator = TokenAuthenticator([args.token]) if args.token else None
    hub = SynapseHub(
        journal=journal,
        rate_limiter=limiter,
        max_history=args.max_history,
        relay_log=args.relay_log,
        relay_max_lines=args.relay_max_lines,
        authenticator=authenticator,
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


async def _send(
    *,
    uri: str,
    name: str,
    target: str,
    message: str,
    wait_seconds: float,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
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

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
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
            token=args.token,
        )
    )


async def _wait(
    *,
    uri: str,
    name: str,
    for_name: str,
    timeout: float,
    directed_only: bool = False,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
) -> int:
    """Block until one message addressed to ``for_name`` arrives, print it, and exit.

    This is the wake primitive: an agent runs it as a background task and the
    moment a message lands the command exits, which re-invokes the agent. The
    connection holds presence while it waits.

    Parameters
    ----------
    uri, name : str
        Hub URI and the connecting identity (keep it distinct from the sender
        name so a waiter and a one-shot ``send`` for the same project never clash).
    for_name : str
        Whose messages to wake on; a chat matches when its target addresses
        ``for_name`` — one agent, a group glob (``quantum/*``), or a broadcast.
    timeout : float
        Seconds to wait; ``0`` waits indefinitely.
    directed_only : bool, optional
        When ``True``, wake only on messages that name ``for_name`` (or a group it
        is in), not on broadcasts — broadcasts are left for a later ``syn-inbox``.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.

    Returns
    -------
    int
        ``0`` when a message arrived, ``1`` when the hub was unreachable, ``2`` on
        timeout with nothing received.
    """
    received: list[dict[str, Any]] = []
    matches = is_directed if directed_only else is_recipient

    async def collect(data: dict[str, Any]) -> None:
        sender = data.get("sender")
        if (
            data.get("type") == MessageType.CHAT
            and sender != name
            and sender != for_name  # ignore our own sends (the agent sends as for_name)
            and matches(str(data.get("target", "all")), for_name)
        ):
            received.append(data)

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while not received and (timeout <= 0 or loop.time() < deadline):
            await asyncio.sleep(0.1)
        if received:
            message = received[-1]
            print(f"{message.get('sender')}: {message.get('payload')}")
            return 0
        return 2
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_wait(args: argparse.Namespace) -> int:
    """Dispatch the ``wait`` subcommand."""
    return asyncio.run(
        _wait(
            uri=args.uri,
            name=args.name,
            for_name=args.for_name or args.name,
            timeout=args.timeout,
            directed_only=args.directed_only,
            token=args.token,
        )
    )


async def _who(
    *,
    uri: str,
    name: str,
    project: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
) -> int:
    """Connect, print the online roster (optionally one project's agents), and exit.

    Discovery for the directory: when several agents share a project their
    identities are ``<project>/<agent>``, so ``--project`` lists exactly the
    instances live on that repo right now.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    project : str or None, optional
        When set, keep only agents named ``project`` or ``project/...``.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.

    Returns
    -------
    int
        ``0`` once a roster is printed, ``1`` when the hub could not be reached.
    """
    rosters: list[list[str]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.WHO_SNAPSHOT:
            rosters.append([str(agent) for agent in data.get("online_agents", [])])

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.request_who()
        for _ in range(50):
            if rosters:
                break
            await asyncio.sleep(0.05)
        if rosters:
            agents = sorted(rosters[-1])
            if project:
                prefix = f"{project}/"
                agents = [a for a in agents if a == project or a.startswith(prefix)]
            label = f"Online in {project}" if project else "Online"
            print(f"{label} ({len(agents)}):")
            for agent_name in agents:
                print(f"  {agent_name}")
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_who(args: argparse.Namespace) -> int:
    """Dispatch the ``who`` subcommand."""
    return asyncio.run(_who(uri=args.uri, name=args.name, project=args.project, token=args.token))


async def _state(
    *,
    uri: str,
    name: str,
    owner: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
) -> int:
    """Print the live claims and their checkpoints — the "where was I" recovery view.

    A returning agent reads this to see what is leased and which tasks carry a
    resume checkpoint, optionally filtered to its own name or project.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    owner : str or None, optional
        Keep only claims owned by this name or project (``owner`` or ``owner/...``).
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.

    Returns
    -------
    int
        ``0`` once the claims are printed, ``1`` when the hub could not be reached.
    """
    snapshots: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.STATE_SNAPSHOT:
            snapshots.append(data.get("snapshot", {}))

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.request_state()
        for _ in range(50):
            if snapshots:
                break
            await asyncio.sleep(0.05)
        if snapshots:
            claims = list(snapshots[-1].get("active_claims", []))
            if owner:
                prefix = f"{owner}/"
                claims = [
                    c
                    for c in claims
                    if c.get("owner") == owner or str(c.get("owner", "")).startswith(prefix)
                ]
            print(f"Active claims ({len(claims)}):")
            for claim in claims:
                paths = ", ".join(claim.get("paths", [])) or "-"
                checkpoint = claim.get("checkpoint") or "-"
                print(
                    f"  {claim.get('task_id')} [{claim.get('status')}] "
                    f"owner={claim.get('owner')} paths={paths} checkpoint={checkpoint}"
                )
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_state(args: argparse.Namespace) -> int:
    """Dispatch the ``state`` subcommand."""
    return asyncio.run(_state(uri=args.uri, name=args.name, owner=args.owner, token=args.token))


LockRunner = Callable[[list[str]], Awaitable[int]]


async def _run_subprocess(command: list[str]) -> int:
    """Run ``command`` and return its exit code (the default lock runner)."""
    proc = await asyncio.create_subprocess_exec(*command)
    return await proc.wait()


async def _lock(
    *,
    uri: str,
    name: str,
    task_id: str,
    command: list[str],
    paths: list[str],
    wait_timeout: float,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    runner: LockRunner = _run_subprocess,
    retry_interval: float = 1.0,
) -> int:
    """Hold a lease on ``task_id`` while running ``command``, serialising it across agents.

    The hub grants only one live lease per task id, so wrapping a commit in
    ``synapse lock <project>:git -- git push`` lets several agents on one repo take
    turns instead of clobbering each other.

    Parameters
    ----------
    uri, name : str
        Hub URI and the connecting identity.
    task_id : str
        The lease key, e.g. ``"quantum:git"``.
    command : list[str]
        The command to run while the lease is held.
    paths : list[str]
        Optional file-scope paths to lock alongside the id.
    wait_timeout : float
        Seconds to keep retrying while another agent holds the lease; ``0`` fails fast.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    runner : LockRunner, optional
        Coroutine that runs the command; injectable for testing.

    Returns
    -------
    int
        The command's exit code, or ``1`` when the hub was unreachable or the lease
        could not be acquired within ``wait_timeout``.
    """
    outcome: dict[str, Any] = {}

    async def collect(data: dict[str, Any]) -> None:
        if data.get("task_id") != task_id:
            return
        if data.get("type") == MessageType.CLAIM_GRANTED and data.get("owner") == name:
            outcome["granted"] = True
        elif data.get("type") == MessageType.CLAIM_DENIED:
            outcome["denied"] = str(data.get("payload") or "held by another agent")

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        loop = asyncio.get_event_loop()
        deadline = loop.time() + wait_timeout
        while True:
            outcome.clear()
            await agent.claim(task_id, paths=paths)
            for _ in range(40):
                if outcome:
                    break
                await asyncio.sleep(0.05)
            if outcome.get("granted"):
                break
            if wait_timeout <= 0 or loop.time() >= deadline:
                print(f"Could not acquire lock '{task_id}': {outcome.get('denied', 'timed out')}")
                return 1
            await asyncio.sleep(retry_interval)
        return await runner(command)
    finally:
        with contextlib.suppress(Exception):
            await agent.release(task_id)
        agent.running = False
        conn_task.cancel()


def _cmd_lock(args: argparse.Namespace) -> int:
    """Dispatch the ``lock`` subcommand."""
    return asyncio.run(
        _lock(
            uri=args.uri,
            name=args.name,
            task_id=args.task_id,
            command=args.command,
            paths=args.paths or [],
            wait_timeout=args.wait_timeout,
            token=args.token,
        )
    )


async def _listen(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    for_name: str | None = None,
) -> int:
    """Stream chat and presence updates to stdout until the connection ends.

    Parameters
    ----------
    uri, name : str
        Hub URI and the listener's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    for_name : str or None, optional
        When set, show only chats addressed to that name (or broadcast) and
        suppress presence updates — a focused per-agent inbox view.

    Returns
    -------
    int
        Always ``0`` once the connection closes.
    """

    async def show(data: dict[str, Any]) -> None:
        msg_type = data.get("type")
        if msg_type == MessageType.CHAT:
            if for_name and not is_recipient(str(data.get("target", "all")), for_name):
                return
            print(f"{data.get('sender')}: {data.get('payload')}")
        elif msg_type == MessageType.PRESENCE_UPDATE and not for_name:
            online = ", ".join(data.get("online_agents", []))
            print(f"[presence] {data.get('event')} -> online: {online}")

    agent = agent_factory(name, show, uri=uri, verbose=True, token=token)
    await agent.connect()
    return 0


def _cmd_listen(args: argparse.Namespace) -> int:
    """Dispatch the ``listen`` subcommand."""
    try:
        return asyncio.run(
            _listen(uri=args.uri, name=args.name, token=args.token, for_name=args.for_name)
        )
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
        message = decode_lite(lite)
        if args.for_name or args.project:
            is_chat = message.get("type") == MessageType.CHAT
            target = str(message.get("target", "all"))
            if args.project:
                keep = is_chat and addresses_project(target, args.project)
            else:
                keep = is_chat and is_recipient(target, args.for_name)
            if not keep:
                continue
        print(_format_relay_line(message))
    if args.cursor:
        save_offset(args.cursor, cursor)
    return 0


def _print_board(board: dict[str, Any]) -> None:
    """Render a blackboard snapshot as readable lines on stdout."""
    tasks = board.get("tasks", [])
    ready = board.get("ready", [])
    progress = board.get("progress", [])
    print(f"Tasks ({len(tasks)}):")
    for task in tasks:
        deps = ", ".join(task.get("depends_on", []))
        suffix = f"  (deps: {deps})" if deps else ""
        print(f"  [{task.get('status')}] {task.get('task_id')} — {task.get('title')}{suffix}")
    print(f"Ready: {', '.join(ready) if ready else '(none)'}")
    if progress:
        print("Recent progress:")
        for note in progress[-10:]:
            task_id = note.get("task_id") or "-"
            print(f"  {note.get('author')} [{note.get('kind')}] {task_id}: {note.get('text')}")


async def _board(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
) -> int:
    """Connect, request the shared blackboard, print it, and exit.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.

    Returns
    -------
    int
        ``0`` once a snapshot is printed, ``1`` when the hub could not be reached.
    """
    boards: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.BOARD_SNAPSHOT:
            boards.append(data.get("board", {}))

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.request_board()
        for _ in range(50):
            if boards:
                break
            await asyncio.sleep(0.05)
        if boards:
            _print_board(boards[-1])
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_board(args: argparse.Namespace) -> int:
    """Dispatch the ``board`` subcommand."""
    return asyncio.run(_board(uri=args.uri, name=args.name, token=args.token))


def _print_manifest(manifest: list[dict[str, Any]]) -> None:
    """Render a capability manifest as readable lines on stdout."""
    print(f"Agents ({len(manifest)}):")
    for card in manifest:
        classes = ", ".join(card.get("task_classes", [])) or "none"
        model = card.get("model") or "-"
        description = card.get("description", "")
        print(f"  {card.get('agent')} [{classes}] model={model}: {description}")


async def _manifest(
    *,
    uri: str,
    name: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
) -> int:
    """Connect, request the capability manifest, print it, and exit.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.

    Returns
    -------
    int
        ``0`` once a manifest is printed, ``1`` when the hub could not be reached.
    """
    manifests: list[list[dict[str, Any]]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.MANIFEST_SNAPSHOT:
            manifests.append(data.get("manifest", []))

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.request_manifest()
        for _ in range(50):
            if manifests:
                break
            await asyncio.sleep(0.05)
        if manifests:
            _print_manifest(manifests[-1])
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_manifest(args: argparse.Namespace) -> int:
    """Dispatch the ``manifest`` subcommand."""
    return asyncio.run(_manifest(uri=args.uri, name=args.name, token=args.token))


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
    seen: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == confirm_type:
            seen.append(data)

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await send(agent)
        for _ in range(60):
            if seen:
                break
            await asyncio.sleep(0.05)
        if seen:
            print(render(seen[-1]))
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


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
    hub.add_argument(
        "--token",
        default=None,
        help="Require this shared-secret token from connecting agents (off by default).",
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

    send = sub.add_parser("send", help="Send one message and optionally await replies.")
    send.add_argument("--uri", default=DEFAULT_HUB_URI)
    send.add_argument("--name", default="USER")
    send.add_argument("--target", default="all")
    send.add_argument("--wait-seconds", type=float, default=2.0)
    send.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    send.add_argument("message")
    send.set_defaults(func=_cmd_send)

    wait = sub.add_parser(
        "wait", help="Block until a message addressed to you arrives, then exit (a wake trigger)."
    )
    wait.add_argument("--uri", default=DEFAULT_HUB_URI)
    wait.add_argument("--name", default="USER")
    wait.add_argument(
        "--for",
        dest="for_name",
        default=None,
        help="Whose messages to wake on (one, a group, or broadcast); defaults to --name.",
    )
    wait.add_argument(
        "--timeout", type=float, default=0.0, help="Seconds to wait; 0 waits indefinitely."
    )
    wait.add_argument(
        "--directed-only",
        action="store_true",
        help="Wake only on messages that name you (or a group you are in), not broadcasts.",
    )
    wait.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    wait.set_defaults(func=_cmd_wait)

    who = sub.add_parser("who", help="List the agents currently online (optionally one project's).")
    who.add_argument("--uri", default=DEFAULT_HUB_URI)
    who.add_argument("--name", default="USER")
    who.add_argument(
        "--project",
        default=None,
        help="Show only agents in this project (matches 'project' or 'project/...').",
    )
    who.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    who.set_defaults(func=_cmd_who)

    state = sub.add_parser(
        "state", help="Print active claims and their checkpoints (a resume view)."
    )
    state.add_argument("--uri", default=DEFAULT_HUB_URI)
    state.add_argument("--name", default="USER")
    state.add_argument(
        "--owner",
        default=None,
        help="Show only claims owned by this name or project (matches 'owner' or 'owner/...').",
    )
    state.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    state.set_defaults(func=_cmd_state)

    lock = sub.add_parser(
        "lock", help="Hold a lease while running a command (serialise e.g. commits)."
    )
    lock.add_argument("task_id")
    lock.add_argument(
        "command", nargs="+", help="The command to run while holding the lease (after --)."
    )
    lock.add_argument("--name", default="USER")
    lock.add_argument(
        "--paths", action="append", default=None, help="File-scope paths to lock (repeatable)."
    )
    lock.add_argument(
        "--wait-timeout",
        type=float,
        default=30.0,
        help="Seconds to keep retrying while another agent holds the lease; 0 fails fast.",
    )
    lock.add_argument("--uri", default=DEFAULT_HUB_URI)
    lock.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    lock.set_defaults(func=_cmd_lock)

    listen = sub.add_parser("listen", help="Stream channel messages until interrupted.")
    listen.add_argument("--uri", default=DEFAULT_HUB_URI)
    listen.add_argument("--name", default="USER")
    listen.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    listen.add_argument(
        "--for",
        dest="for_name",
        default=None,
        help="Show only chats addressed to this name (or broadcast) and suppress "
        "presence updates — a focused per-agent inbox.",
    )
    listen.set_defaults(func=_cmd_listen)

    relay = sub.add_parser("relay", help="Decode and print a hub's lite relay log.")
    relay.add_argument("relay_log", help="Path to the lite relay log to read.")
    relay.add_argument("--since", type=int, default=0, help="Byte offset to start reading from.")
    relay.add_argument(
        "--cursor",
        default=None,
        help="File holding a persisted read offset; resumes where the last run left off.",
    )
    relay.add_argument(
        "--for",
        dest="for_name",
        default=None,
        help="Show only chats addressed to this name (or broadcast), dropping other "
        "traffic and presence noise — a per-agent inbox view.",
    )
    relay.add_argument(
        "--project",
        default=None,
        help="Show chats addressing any agent in this project (the name, 'project/...', "
        "or a broadcast) — a project-stable inbox that survives changing instance ids.",
    )
    relay.set_defaults(func=_cmd_relay)

    board = sub.add_parser("board", help="Print the hub's shared task/progress board.")
    board.add_argument("--uri", default=DEFAULT_HUB_URI)
    board.add_argument("--name", default="USER")
    board.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    board.set_defaults(func=_cmd_board)

    supervisor = sub.add_parser(
        "supervisor", help="Run an LLM-free supervisor that re-offers stalled tasks."
    )
    supervisor.add_argument("--uri", default=DEFAULT_HUB_URI)
    supervisor.add_argument("--name", default="SUPERVISOR")
    supervisor.add_argument("--idle-seconds", type=float, default=DEFAULT_IDLE_SECONDS)
    supervisor.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    supervisor.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    supervisor.set_defaults(func=_cmd_supervisor)

    manifest = sub.add_parser("manifest", help="Print the capability manifest of agents.")
    manifest.add_argument("--uri", default=DEFAULT_HUB_URI)
    manifest.add_argument("--name", default="USER")
    manifest.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    manifest.set_defaults(func=_cmd_manifest)

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
