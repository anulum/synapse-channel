# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — lease-serialising CLI commands (lock, release)
"""The lease-oriented ``synapse`` subcommands.

``lock`` holds a hub lease while running a wrapped command so several agents on
one repo take turns instead of clobbering each other, and ``release`` manually
drops a claim the caller owns (the escape hatch for an ``--auto-release-on
manual`` claim). Both open their own short-lived client and watch for the hub's
grant/deny verdict rather than sharing the read-side query plumbing, so they live
here apart from the hub-query verbs; :func:`add_parsers` registers their
subparsers on the top-level CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.protocol import MessageType

AgentFactory = Callable[..., SynapseAgent]
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
    ready_timeout: float = 5.0,
    attempts: int = 40,
    poll_interval: float = 0.05,
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
    retry_interval : float, optional
        Seconds to wait between denied claim attempts.
    ready_timeout : float, optional
        Seconds to wait for the hub connection readiness event.
    attempts : int, optional
        Claim verdict polling attempts per claim request.
    poll_interval : float, optional
        Seconds to wait between verdict polls.

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
        if not await agent.wait_until_ready(timeout=ready_timeout):
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
            for _ in range(attempts):
                if outcome:
                    break
                await asyncio.sleep(poll_interval)
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
            ready_timeout=args.ready_timeout,
        )
    )


async def _release(
    *,
    uri: str,
    name: str,
    task_id: str,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
    attempts: int = 40,
    poll_interval: float = 0.05,
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
    ready_timeout : float, optional
        Seconds to wait for the hub connection readiness event.
    attempts : int, optional
        Release verdict polling attempts.
    poll_interval : float, optional
        Seconds to wait between verdict polls.

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
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.release(task_id)
        for _ in range(attempts):
            if outcome:
                break
            await asyncio.sleep(poll_interval)
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
        _release(
            uri=args.uri,
            name=args.name,
            task_id=args.task_id,
            token=args.token,
            ready_timeout=args.ready_timeout,
        )
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``lock`` and ``release`` subparsers on the top-level CLI."""
    lock = subparsers.add_parser(
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
    lock.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    lock.set_defaults(func=_cmd_lock)

    release = subparsers.add_parser(
        "release", help="Manually drop a claim you own (e.g. an --auto-release-on manual claim)."
    )
    release.add_argument("task_id")
    release.add_argument(
        "--name", default="USER", help="The releasing identity; must own the claim."
    )
    release.add_argument("--uri", default=DEFAULT_HUB_URI)
    release.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    release.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    release.set_defaults(func=_cmd_release)
