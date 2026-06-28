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
:class:`~synapse_channel.client.agent.SynapseAgent`.

The git subprocess is injectable (``runner``) so the flow is unit-testable
without a real repository.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import describe_connect_failure, explain_silent_outcome
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.state import GitContext
from synapse_channel.git.semantic_claims import (
    SemanticClaimRecord,
    records_to_json,
    resolve_selectors,
)

GitRunner = Callable[[list[str]], str]
"""Runs a git subcommand (argv after ``git``) and returns its stripped stdout."""

AgentFactory = Callable[..., SynapseAgent]
"""Factory that builds the hub client; injectable for testing."""


class GitError(RuntimeError):
    """A git command failed, or git is not available on the host."""


def _unique_ordered(values: list[str]) -> list[str]:
    """Return values without duplicates while preserving first-seen order."""
    return list(dict.fromkeys(values))


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
    git = shutil.which("git")
    if git is None:
        raise GitError("git is not installed or not on PATH")
    try:
        # Fixed git binary, no shell, bounded argv from internal git operations.
        result = subprocess.run(  # nosec B603
            [git, *args],
            capture_output=True,
            text=True,
            check=True,
        )
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


def resolve_repo(*, runner: GitRunner = _default_git_runner) -> str:
    """Return the repository root via ``git rev-parse --show-toplevel``.

    The root path labels the claim's worktree so that claims in *different*
    repositories never contend: a git-scoped claim must isolate its file scope to
    its own repository, never to the hub's shared default tree. Two linked git
    worktrees over one ``.git`` resolve to distinct roots and so stay isolated
    too, which is the worktree-isolation contract the scope model already honours.

    Parameters
    ----------
    runner : GitRunner, optional
        The git executor; injectable for testing.

    Returns
    -------
    str
        The absolute path of the repository's top-level directory.

    Raises
    ------
    GitError
        When the git command fails.
    """
    return runner(["rev-parse", "--show-toplevel"])


def _warn_if_auto_release_unbacked(
    auto_release_on: str, task_id: str, name: str, *, runner: GitRunner
) -> None:
    """Warn when a claim's auto-release trigger has no git hook to enact it.

    ``auto_release_on commit/merge`` is enacted only by the client-side git hook,
    never by the hub, so without ``synapse git-hook`` installed the claim sits held
    until it is dropped manually. The note points at both remedies — install the
    hook, or release it by hand — so the banner never implies an automation that is
    not actually wired. (Imported lazily because :mod:`synapse_channel.git.githook`
    imports this module.)
    """
    if auto_release_on not in ("commit", "merge"):
        return
    from synapse_channel.git.githook import hook_installed

    if hook_installed(auto_release_on, runner=runner):
        return
    print(
        f"  note: auto-release on {auto_release_on} is enacted by a git hook that is "
        f"not installed in this clone — it will NOT fire. Run `synapse git-hook` once "
        f"to enable it, or drop the claim manually with "
        f"`synapse release {task_id} --name {name}`."
    )


def _write_semantic_evidence(
    records: tuple[SemanticClaimRecord, ...],
    repo_root: Path,
    evidence_json: str,
) -> None:
    """Write receipt-ready semantic selector evidence below ``repo_root``.

    Parameters
    ----------
    records : tuple[SemanticClaimRecord, ...]
        Resolved selector records to serialise.
    repo_root : Path
        Local git root used to resolve relative evidence paths.
    evidence_json : str
        Destination path. Relative paths are interpreted under ``repo_root``.
    """
    destination = Path(evidence_json)
    if not destination.is_absolute():
        destination = repo_root / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(records_to_json(records), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _semantic_claim_paths(
    *,
    repo_root: Path,
    selectors: tuple[str, ...],
    evidence_json: str | None,
) -> list[str]:
    """Resolve semantic selectors into ordinary claim paths.

    Parameters
    ----------
    repo_root : Path
        Local git repository root.
    selectors : tuple[str, ...]
        Semantic ``kind:value`` selectors passed by the CLI.
    evidence_json : str or None
        Optional path where receipt-ready selector evidence should be written.

    Returns
    -------
    list[str]
        Unique claim paths derived from the semantic selector records.

    Raises
    ------
    ValueError
        When a selector cannot be resolved.
    OSError
        When selector evidence cannot be written.
    """
    if not selectors:
        return []
    records = resolve_selectors(repo_root, selectors)
    if evidence_json:
        _write_semantic_evidence(records, repo_root, evidence_json)
    paths = [path for record in records for path in record.claim_paths]
    print(
        "semantic selectors resolved: "
        f"{len(records)} selector(s), {len(_unique_ordered(paths))} claim path(s)"
    )
    return _unique_ordered(paths)


async def run_git_claim(
    *,
    uri: str,
    name: str,
    task_id: str,
    paths: list[str],
    base: str = "main",
    auto_release_on: str = "merge",
    token: str | None = None,
    semantic_selectors: tuple[str, ...] = (),
    semantic_evidence_json: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    runner: GitRunner = _default_git_runner,
    ready_timeout: float = 5.0,
    attempts: int = 40,
    poll_interval: float = 0.05,
) -> int:
    """Resolve the current branch and send a git-scoped claim, printing the outcome.

    The branch is resolved locally and carried as a :class:`GitContext` on an
    ordinary claim; the hub treats it as opaque metadata. The repository root is
    also resolved locally and set as the claim's worktree, so a git-scoped claim
    is isolated to its own repository and never contends with a claim in another
    repository (even one declaring identically-named paths).

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
    semantic_selectors : tuple[str, ...], optional
        Client-side semantic selectors in ``kind:value`` form. They are resolved
        against the local git root into ordinary ``paths`` before the claim is
        sent; the hub never receives semantic selectors.
    semantic_evidence_json : str or None, optional
        Optional destination for receipt-ready selector evidence JSON. Relative
        paths are written below the resolved git root.
    agent_factory : AgentFactory, optional
        Factory for the hub client; injectable for testing.
    runner : GitRunner, optional
        The git executor; injectable for testing.
    ready_timeout : float, optional
        Seconds to wait for the hub connection readiness event.
    attempts : int, optional
        Number of claim outcome polling attempts.
    poll_interval : float, optional
        Seconds to sleep between claim outcome polls.

    Returns
    -------
    int
        ``0`` on a granted claim; ``1`` when git fails, the hub is unreachable,
        or the claim is denied.
    """
    try:
        branch = resolve_branch(runner=runner)
        repo = resolve_repo(runner=runner)
    except GitError as exc:
        print(f"git error: {exc}")
        return 1
    repo_root = Path(repo)
    try:
        semantic_paths = _semantic_claim_paths(
            repo_root=repo_root,
            selectors=semantic_selectors,
            evidence_json=semantic_evidence_json,
        )
    except ValueError as exc:
        print(f"semantic claim error: {exc}")
        return 1
    except OSError as exc:
        print(f"semantic claim evidence error: {exc}")
        return 1
    claim_paths = _unique_ordered([*paths, *semantic_paths])
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
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                describe_connect_failure(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1
        await agent.claim(task_id, worktree=repo, paths=claim_paths, git=context.as_dict())
        for _ in range(attempts):
            if outcome or conn_task.done():
                break
            await asyncio.sleep(poll_interval)
        if outcome.get("granted"):
            print(
                f"claimed '{task_id}' on branch {branch} "
                f"(base {base}, auto-release on {auto_release_on})"
            )
            _warn_if_auto_release_unbacked(auto_release_on, task_id, name, runner=runner)
            return 0
        denied = outcome.get("denied")
        if denied:
            print(f"claim denied for '{task_id}': {denied}")
        else:
            print(
                explain_silent_outcome(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                    fallback=f"claim denied for '{task_id}': no response from hub",
                )
            )
        return 1
    finally:
        agent.running = False
        conn_task.cancel()
