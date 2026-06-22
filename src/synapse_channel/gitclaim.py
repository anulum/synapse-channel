# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — client-side git integration for branch-scoped claims
"""Client-side git integration for branch-scoped claims.

All git execution lives here, on the client side of the bus. The hub never runs
git or reads a filesystem: a git-aware agent resolves its current branch locally
and attaches the result as opaque metadata on an ordinary claim, so the hub can
display and group claims by branch without ever touching a repository. This
module resolves the branch and drives a git-scoped claim through a
:class:`~synapse_channel.client.SynapseAgent`.

The git subprocess is injectable (``runner``) so the flow is unit-testable
without a real repository.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from typing import Any

from synapse_channel.client import SynapseAgent
from synapse_channel.protocol import MessageType
from synapse_channel.state import GitContext

GitRunner = Callable[[list[str]], str]
"""Runs a git subcommand (argv after ``git``) and returns its stripped stdout."""

AgentFactory = Callable[..., SynapseAgent]
"""Factory that builds the hub client; injectable for testing."""


class GitError(RuntimeError):
    """A git command failed, or git is not available on the host."""


def _default_git_runner(args: list[str]) -> str:
    """Run ``git <args>`` in the current directory and return stripped stdout.

    Parameters
    ----------
    args : list[str]
        The git subcommand and its arguments (everything after ``git``).

    Returns
    -------
    str
        The command's standard output with surrounding whitespace removed.

    Raises
    ------
    GitError
        When git is not installed or the command exits non-zero.
    """
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"git {' '.join(args)} exited non-zero"
        raise GitError(detail) from exc
    return result.stdout.strip()


def resolve_branch(*, runner: GitRunner = _default_git_runner) -> str:
    """Return the current branch via ``git rev-parse --abbrev-ref HEAD``.

    Parameters
    ----------
    runner : GitRunner, optional
        The git executor; injectable for testing.

    Returns
    -------
    str
        The current branch name (``HEAD`` when detached).

    Raises
    ------
    GitError
        When the git command fails.
    """
    return runner(["rev-parse", "--abbrev-ref", "HEAD"])


async def run_git_claim(
    *,
    uri: str,
    name: str,
    task_id: str,
    paths: list[str],
    base: str = "main",
    auto_release_on: str = "merge",
    token: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    runner: GitRunner = _default_git_runner,
) -> int:
    """Resolve the current branch and send a git-scoped claim, printing the outcome.

    The branch is resolved locally and carried as a :class:`GitContext` on an
    ordinary claim; the hub treats it as opaque metadata.

    Parameters
    ----------
    uri, name : str
        Hub URI and the claiming agent's identity.
    task_id : str
        Identifier of the task to claim.
    paths : list[str]
        File-scope paths to declare on the claim.
    base : str, optional
        The branch the work merges back into. Defaults to ``main``.
    auto_release_on : str, optional
        The declared auto-release trigger (``manual``/``commit``/``merge``) a
        git hook will later enact. Defaults to ``merge``.
    token : str or None, optional
        Shared-secret token for a secured hub.
    agent_factory : AgentFactory, optional
        Factory for the hub client; injectable for testing.
    runner : GitRunner, optional
        The git executor; injectable for testing.

    Returns
    -------
    int
        ``0`` on a granted claim; ``1`` when git fails, the hub is unreachable,
        or the claim is denied.
    """
    try:
        branch = resolve_branch(runner=runner)
    except GitError as exc:
        print(f"git error: {exc}")
        return 1
    context = GitContext(branch=branch, base=base, auto_release_on=auto_release_on)

    outcome: dict[str, Any] = {}

    async def collect(data: dict[str, Any]) -> None:
        if data.get("task_id") != task_id:
            return
        if data.get("type") == MessageType.CLAIM_GRANTED and data.get("owner") == name:
            outcome["granted"] = True
        elif data.get("type") == MessageType.CLAIM_DENIED:
            outcome["denied"] = str(data.get("payload") or "claim denied")

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 1
        await agent.claim(task_id, paths=paths, git=context.as_dict())
        for _ in range(40):
            if outcome:
                break
            await asyncio.sleep(0.05)
        if outcome.get("granted"):
            print(
                f"claimed '{task_id}' on branch {branch} "
                f"(base {base}, auto-release on {auto_release_on})"
            )
            return 0
        print(f"claim denied for '{task_id}': {outcome.get('denied', 'no response from hub')}")
        return 1
    finally:
        agent.running = False
        conn_task.cancel()
