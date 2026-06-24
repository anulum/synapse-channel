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

The send/listen helpers take an injectable agent factory so the dispatch and the
client flows are unit-testable without a live hub.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import random
import sys
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel import __version__
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
    addresses_project,
    is_recipient,
    wakes,
)
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.git.gitclaim import GitError, run_git_claim
from synapse_channel.git.gitconflict import run_conflicts
from synapse_channel.git.githook import install_hooks, run_git_release
from synapse_channel.mcp.server import DEFAULT_BRIDGE_NAME, serve_stdio
from synapse_channel.relay import decode_lite, load_offset, read_jsonl_since, save_offset
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
    priority: bool = False,
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
    priority : bool, optional
        Mark the message as priority so it wakes even directed-only waiters.
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
        await agent.chat(message, target=target, priority=priority)
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
            priority=args.priority,
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
    wake_jitter: float = 0.0,
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
    wake_jitter : float, optional
        Seconds of random delay added before exiting on a *broadcast* wake (a
        message to ``all`` or a glob/list that reaches many waiters). A broadcast
        wakes every terminal at once; without jitter their agents all re-invoke in
        the same instant and the provider rate-limits the burst. Jitter spreads the
        wakes over ``[0, wake_jitter]`` so each reacts but not simultaneously. A
        one-to-one directed message wakes immediately (no herd). ``0`` disables it.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.

    Returns
    -------
    int
        ``0`` when a message arrived, ``1`` when the hub was unreachable, ``2`` on
        timeout with nothing received, ``3`` when the connection dropped while
        waiting (so the caller knows to re-arm rather than treat it as a timeout).
    """
    received: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        sender = str(data.get("sender", ""))
        if (
            data.get("type") == MessageType.CHAT
            and sender != name
            and sender != for_name  # ignore our own sends (the agent sends as for_name)
            and wakes(
                str(data.get("target", "all")),
                for_name,
                directed_only=directed_only,
                sender=sender,
                priority=bool(data.get("priority")),
            )
        ):
            received.append(data)

    # A re-arming waiter takes over its own name, evicting a ghost holder of
    # ``<name>-rx`` instead of failing with a name conflict.
    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token, takeover=True)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while not received and (timeout <= 0 or loop.time() < deadline):
            if conn_task.done():
                break  # the socket closed (hub restart, superseded, network)
            await asyncio.sleep(0.1)
        if received:
            message = received[-1]
            target = str(message.get("target", "all")).strip()
            # A broadcast woke many terminals at the same instant; jitter the exit
            # so their agents do not all re-invoke (and hit the provider API)
            # simultaneously and get rate-limited. A 1:1 directed message has no
            # herd, so it wakes now.
            reaches_many = target in ("", "all") or "*" in target or "," in target
            if reaches_many and wake_jitter > 0:
                await asyncio.sleep(random.uniform(0.0, wake_jitter))
            print(f"{message.get('sender')}: {message.get('payload')}")
            return 0
        if conn_task.done():
            # The connection dropped without a message. Exit so the caller re-arms,
            # rather than looping forever on a dead socket — a timeout=0 waiter that
            # silently stayed up after a hub restart is exactly how an agent goes dark.
            print(f"[{name}] connection to {uri} closed; re-arm the waiter.")
            return 3
        return 2
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_wait(args: argparse.Namespace) -> int:
    """Dispatch the ``wait`` subcommand.

    The waiter connects only to *receive*, so its connection name must never be the
    bare identity it waits for — otherwise it holds that name and the agent's own
    sends (which use the same identity) are refused with a name conflict. When the
    two would coincide, the connection name is suffixed with ``-rx``.
    """
    for_name = args.for_name or args.name
    connect_name = args.name if args.name != for_name else f"{args.name}-rx"
    return asyncio.run(
        _wait(
            uri=args.uri,
            name=connect_name,
            for_name=for_name,
            timeout=args.timeout,
            directed_only=args.directed_only,
            wake_jitter=args.wake_jitter,
            token=args.token,
        )
    )


async def _drop_message(_data: dict[str, Any]) -> None:
    """Discard a hub message — for probes that only need the connection to open."""
    return None


async def _health(
    *,
    uri: str,
    name: str = "HEALTH",
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
) -> int:
    """Connect and report whether the hub is reachable: ``0`` if so, ``1`` if not.

    A quiet liveness probe for container healthchecks — it opens a connection, waits
    for the welcome handshake, and exits without printing on success.

    Parameters
    ----------
    uri, name : str
        Hub URI and the probe's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.

    Returns
    -------
    int
        ``0`` when the hub answered, ``1`` otherwise.
    """
    agent = agent_factory(name, _drop_message, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        return 0 if await agent.wait_until_ready(timeout=5.0) else 1
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_health(args: argparse.Namespace) -> int:
    """Probe the hub and return its reachability as the process exit code."""
    return asyncio.run(_health(uri=args.uri, name=args.name, token=args.token))


def _cmd_mcp(args: argparse.Namespace) -> int:
    """Run the Model Context Protocol server over stdio, bridged to the hub.

    Exposes the hub's coordination verbs to any MCP client. Requires the optional
    ``mcp`` extra; a missing extra is reported with the install hint and exit ``1``.
    """
    try:
        return asyncio.run(serve_stdio(uri=args.uri, name=args.name, token=args.token))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"\n[{args.name}] MCP server stopped.")
        return 0


def _identity(data: dict[str, Any]) -> Any:
    """Return the message unchanged — the default reply transform for a query."""
    return data


async def _query_hub(
    *,
    uri: str,
    name: str,
    token: str | None,
    response_type: str,
    request: Callable[[SynapseAgent], Awaitable[None]],
    render: Callable[[Any], None],
    transform: Callable[[dict[str, Any]], Any] = _identity,
    agent_factory: AgentFactory = SynapseAgent,
    attempts: int = 50,
) -> int:
    """Connect, issue one request, await the matching reply, render it, and exit.

    The shared connect → ready → request → poll → cleanup flow behind ``who``,
    ``state``, ``board``, ``manifest``, and the task writes; a caller supplies only
    the reply ``response_type``, how to ``request`` it, what to ``render``, and an
    optional ``transform`` from the raw message to the rendered value.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    token : str or None
        Shared-secret token for a secured hub.
    response_type : str
        The inbound message type that answers the request.
    request : Callable
        Coroutine that issues the request on the connected agent.
    render : Callable
        Renders the latest (transformed) reply; it prints and returns nothing.
    transform : Callable, optional
        Maps the raw reply to the value handed to ``render``. Identity by default.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    attempts : int, optional
        Poll attempts (50 ms each) for the reply before giving up. Defaults to ``50``.

    Returns
    -------
    int
        ``0`` once a reply is rendered (or none arrives), ``1`` when the hub is unreachable.
    """
    results: list[Any] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == response_type:
            results.append(transform(data))

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await request(agent)
        for _ in range(attempts):
            if results:
                break
            await asyncio.sleep(0.05)
        if results:
            render(results[-1])
        return 0
    finally:
        agent.running = False
        conn_task.cancel()


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

    def render(roster: list[str]) -> None:
        agents = sorted(roster)
        if project:
            prefix = f"{project}/"
            agents = [a for a in agents if a == project or a.startswith(prefix)]
        label = f"Online in {project}" if project else "Online"
        print(f"{label} ({len(agents)}):")
        for agent_name in agents:
            print(f"  {agent_name}")

    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.WHO_SNAPSHOT,
        transform=lambda data: [str(agent) for agent in data.get("online_agents", [])],
        request=lambda agent: agent.request_who(),
        render=render,
    )


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

    def render(snapshot: dict[str, Any]) -> None:
        claims = list(snapshot.get("active_claims", []))
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
            git = claim.get("git")
            git_suffix = f" git={git['branch']}->{git['base']}" if git else ""
            print(
                f"  {claim.get('task_id')} [{claim.get('status')}] "
                f"owner={claim.get('owner')} paths={paths} checkpoint={checkpoint}{git_suffix}"
            )

    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.STATE_SNAPSHOT,
        transform=lambda data: data.get("snapshot", {}),
        request=lambda agent: agent.request_state(),
        render=render,
    )


def _cmd_state(args: argparse.Namespace) -> int:
    """Dispatch the ``state`` subcommand."""
    return asyncio.run(_state(uri=args.uri, name=args.name, owner=args.owner, token=args.token))


def _cmd_git_claim(args: argparse.Namespace) -> int:
    """Dispatch the ``git-claim`` subcommand: a claim scoped to the current git branch.

    The branch is resolved client-side; the hub stores it as opaque metadata and
    never runs git itself.
    """
    return asyncio.run(
        run_git_claim(
            uri=args.uri,
            name=args.name,
            task_id=args.task_id,
            paths=args.paths or [],
            base=args.base,
            auto_release_on=args.auto_release_on,
            token=args.token,
        )
    )


def _cmd_git_hook(args: argparse.Namespace) -> int:
    """Install git hooks that auto-release branch-scoped claims on commit/merge.

    The hooks are written client-side and call ``synapse git-release``; the hub is
    never involved in installing or running them.
    """
    try:
        lines = install_hooks(
            uri=args.uri, name=args.name, token_file=getattr(args, "token_file", None)
        )
    except GitError as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


def _cmd_git_release(args: argparse.Namespace) -> int:
    """Release branch-scoped claims whose paths were just committed or merged.

    Invoked by the installed git hooks; resolves the changed files client-side and
    sends an ordinary release for each matching claim.
    """
    return asyncio.run(
        run_git_release(uri=args.uri, name=args.name, trigger=args.trigger, token=args.token)
    )


def _cmd_conflicts(args: argparse.Namespace) -> int:
    """Predict merge conflicts between branch-scoped claims on different branches.

    Reads the hub's live claims and flags cross-branch path overlaps; ``--check-diff``
    refines the prediction against each branch's actual ``git diff``. All git work is
    client-side.
    """
    return asyncio.run(
        run_conflicts(uri=args.uri, name=args.name, token=args.token, check_diff=args.check_diff)
    )


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
    turns instead of clobbering each other. A lock with no explicit ``paths`` is a
    pure named mutex: its claim is namespaced to its own task id, so two different
    locks never contend (one repo's ``:git`` push-lock cannot block another repo's
    lock). Passing ``paths`` opts into shared file-scope overlap instead.

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
        # A keyless lock (no explicit --paths) is a pure named mutex: scope its
        # claim to its own task-id namespace so two different locks never contend
        # for the hub's shared default worktree (a `<repo>:git` push-lock must not
        # block an unrelated repo's lock or claim). With explicit paths the caller
        # wants real file-scope overlap, so the claim stays in the shared tree
        # where declared paths are compared.
        lock_worktree = "" if paths else task_id
        while True:
            outcome.clear()
            await agent.claim(task_id, worktree=lock_worktree, paths=paths)
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


async def _release(
    *,
    uri: str,
    name: str,
    task_id: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
) -> int:
    """Drop a claim the caller owns, printing the hub's verdict.

    The manual escape hatch for a claim that no automatic trigger will release —
    a ``git-claim --auto-release-on manual``, or any lease whose holder simply
    wants to let go. The hub only honours a release from the claim's owner, so
    ``--name`` must match the owner recorded on the claim.

    Parameters
    ----------
    uri, name : str
        Hub URI and the releasing identity; must equal the claim's owner.
    task_id : str
        Identifier of the claim to release.
    agent_factory : AgentFactory, optional
        Factory for the hub client; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.

    Returns
    -------
    int
        ``0`` when the hub confirms the release; ``1`` when the hub is unreachable,
        denies the release (not the owner, or no such claim), or stays silent.
    """
    outcome: dict[str, Any] = {}

    async def collect(data: dict[str, Any]) -> None:
        if str(data.get("task_id")) != task_id:
            return
        if data.get("type") == MessageType.RELEASE_GRANTED and data.get("owner") == name:
            outcome["released"] = True
        elif data.get("type") == MessageType.RELEASE_DENIED:
            outcome["denied"] = str(data.get("payload") or "release denied")

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.release(task_id)
        for _ in range(40):
            if outcome:
                break
            await asyncio.sleep(0.05)
        if outcome.get("released"):
            print(f"released '{task_id}'")
            return 0
        print(f"release refused for '{task_id}': {outcome.get('denied', 'no response from hub')}")
        return 1
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_release(args: argparse.Namespace) -> int:
    """Dispatch the ``release`` subcommand: manually drop an owned claim."""
    return asyncio.run(
        _release(uri=args.uri, name=args.name, task_id=args.task_id, token=args.token)
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
    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.BOARD_SNAPSHOT,
        transform=lambda data: data.get("board", {}),
        request=lambda agent: agent.request_board(),
        render=_print_board,
    )


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
    return await _query_hub(
        uri=uri,
        name=name,
        token=token,
        agent_factory=agent_factory,
        response_type=MessageType.MANIFEST_SNAPSHOT,
        transform=lambda data: data.get("manifest", []),
        request=lambda agent: agent.request_manifest(),
        render=_print_manifest,
    )


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
    send.add_argument(
        "--priority",
        action="store_true",
        help="Mark as priority so it wakes even directed-only waiters (use sparingly).",
    )
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
    wait.add_argument(
        "--wake-jitter",
        type=float,
        default=8.0,
        help="Random seconds (0..N) to delay exiting on a broadcast wake, so many "
        "terminals do not re-invoke at once and trip the provider rate limit; 0 disables.",
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

    health = sub.add_parser("health", help="Probe the hub; exit 0 if reachable, 1 if not.")
    health.add_argument("--uri", default=DEFAULT_HUB_URI)
    health.add_argument("--name", default="HEALTH")
    health.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    health.set_defaults(func=_cmd_health)

    mcp = sub.add_parser(
        "mcp",
        help="Run an MCP server over stdio that bridges to the hub (needs the [mcp] extra).",
    )
    mcp.add_argument("--uri", default=DEFAULT_HUB_URI)
    mcp.add_argument("--name", default=DEFAULT_BRIDGE_NAME)
    mcp.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    mcp.set_defaults(func=_cmd_mcp)

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

    git_claim = sub.add_parser(
        "git-claim",
        help="Claim a task scoped to the current git branch (branch resolved client-side).",
    )
    git_claim.add_argument("task_id")
    git_claim.add_argument(
        "--paths",
        action="append",
        default=None,
        help="File-scope path the claim intends to touch (repeatable).",
    )
    git_claim.add_argument(
        "--base", default="main", help="Branch the work merges back into (default: main)."
    )
    git_claim.add_argument(
        "--auto-release-on",
        choices=["manual", "commit", "merge"],
        default="merge",
        help="When a git hook should release the claim; enacted by 'synapse git-hook'.",
    )
    git_claim.add_argument("--uri", default=DEFAULT_HUB_URI)
    git_claim.add_argument("--name", default="USER")
    git_claim.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    git_claim.set_defaults(func=_cmd_git_claim)

    git_hook = sub.add_parser(
        "git-hook",
        help="Install git hooks that auto-release branch-scoped claims on commit/merge.",
    )
    git_hook.add_argument("action", choices=["install"], help="The hook action to perform.")
    git_hook.add_argument("--uri", default=DEFAULT_HUB_URI)
    git_hook.add_argument("--name", default="USER")
    git_hook.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    git_hook.set_defaults(func=_cmd_git_hook)

    git_release = sub.add_parser(
        "git-release",
        help="Release branch-scoped claims whose paths were committed/merged (used by git hooks).",
    )
    git_release.add_argument(
        "--trigger",
        choices=["commit", "merge"],
        required=True,
        help="Which auto-release trigger fired.",
    )
    git_release.add_argument("--uri", default=DEFAULT_HUB_URI)
    git_release.add_argument("--name", default="USER")
    git_release.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    git_release.set_defaults(func=_cmd_git_release)

    conflicts = sub.add_parser(
        "conflicts",
        help="Predict merge conflicts between branch-scoped claims on different branches.",
    )
    conflicts.add_argument(
        "--check-diff",
        action="store_true",
        help="Refine the prediction against each branch's actual 'git diff base...branch'.",
    )
    conflicts.add_argument("--uri", default=DEFAULT_HUB_URI)
    conflicts.add_argument("--name", default="USER")
    conflicts.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    conflicts.set_defaults(func=_cmd_conflicts)

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

    release = sub.add_parser(
        "release", help="Manually drop a claim you own (e.g. an --auto-release-on manual claim)."
    )
    release.add_argument("task_id")
    release.add_argument(
        "--name", default="USER", help="The releasing identity; must own the claim."
    )
    release.add_argument("--uri", default=DEFAULT_HUB_URI)
    release.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    release.set_defaults(func=_cmd_release)

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
