# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only hub query CLI commands (who, state, board, manifest, health)
"""The read-only hub-query ``synapse`` subcommands.

These commands connect to a live hub, issue one request, render the reply, and
exit — they read hub state rather than mutating it or exchanging chat: ``who``
lists the online roster, ``state`` prints active claims and their checkpoints,
``board`` prints the shared task/progress blackboard, ``manifest`` prints the
advertised capability cards, and ``health`` reports reachability as the exit
code. They share one connect → ready → request → poll → render flow,
:func:`_query_hub`, which the task-write commands also reuse. They are grouped
here, apart from the messaging and hub-lifecycle commands, so each module stays
one responsibility; :func:`add_parsers` registers their subparsers on the
top-level CLI.

The query helpers take an injectable agent factory so the dispatch and the
client flows are unit-testable without a live hub.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.protocol import MessageType

AgentFactory = Callable[..., SynapseAgent]


async def _drop_message(_data: dict[str, Any]) -> None:
    """Discard a hub message — for probes that only need the connection to open."""
    return None


async def _health(
    *,
    uri: str,
    name: str = "HEALTH",
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
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
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

    Returns
    -------
    int
        ``0`` when the hub answered, ``1`` otherwise.
    """
    agent = agent_factory(name, _drop_message, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        return 0 if await agent.wait_until_ready(timeout=ready_timeout) else 1
    finally:
        agent.running = False
        conn_task.cancel()


def _cmd_health(args: argparse.Namespace) -> int:
    """Probe the hub and return its reachability as the process exit code."""
    return asyncio.run(_health(uri=args.uri, name=args.name, token=args.token))


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
    ready_timeout: float = 5.0,
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
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

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
        if not await agent.wait_until_ready(timeout=ready_timeout):
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
    ready_timeout: float = 5.0,
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
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

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
        ready_timeout=ready_timeout,
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
    ready_timeout: float = 5.0,
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
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

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
        ready_timeout=ready_timeout,
    )


def _cmd_state(args: argparse.Namespace) -> int:
    """Dispatch the ``state`` subcommand."""
    return asyncio.run(_state(uri=args.uri, name=args.name, owner=args.owner, token=args.token))


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
    ready_timeout: float = 5.0,
) -> int:
    """Connect, request the shared blackboard, print it, and exit.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.

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
        ready_timeout=ready_timeout,
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


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``who``, ``health``, ``state``, ``board``, and ``manifest`` subparsers."""
    who = subparsers.add_parser(
        "who", help="List the agents currently online (optionally one project's)."
    )
    who.add_argument("--uri", default=DEFAULT_HUB_URI)
    who.add_argument("--name", default="USER")
    who.add_argument(
        "--project",
        default=None,
        help="Show only agents in this project (matches 'project' or 'project/...').",
    )
    who.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    who.set_defaults(func=_cmd_who)

    health = subparsers.add_parser("health", help="Probe the hub; exit 0 if reachable, 1 if not.")
    health.add_argument("--uri", default=DEFAULT_HUB_URI)
    health.add_argument("--name", default="HEALTH")
    health.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    health.set_defaults(func=_cmd_health)

    state = subparsers.add_parser(
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

    board = subparsers.add_parser("board", help="Print the hub's shared task/progress board.")
    board.add_argument("--uri", default=DEFAULT_HUB_URI)
    board.add_argument("--name", default="USER")
    board.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    board.set_defaults(func=_cmd_board)

    manifest = subparsers.add_parser("manifest", help="Print the capability manifest of agents.")
    manifest.add_argument("--uri", default=DEFAULT_HUB_URI)
    manifest.add_argument("--name", default="USER")
    manifest.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    manifest.set_defaults(func=_cmd_manifest)
