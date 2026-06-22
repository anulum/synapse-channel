# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — git hooks that auto-release branch-scoped claims
"""Git hook integration that auto-releases branch-scoped claims.

A companion to :mod:`synapse_channel.gitclaim`. ``synapse git-hook install``
writes ``post-commit`` and ``post-merge`` hooks that call ``synapse git-release``,
which releases this agent's branch-scoped claims whose declared paths were just
committed or merged. As everywhere in the git integration, the work is entirely
client-side: the hub only ever receives an ordinary release and never learns that
git was involved. Both the git executor and the file resolution are injectable so
the flow is unit-testable without a real repository.
"""

from __future__ import annotations

import asyncio
import shlex
import stat
from pathlib import Path
from typing import Any

from synapse_channel.client import SynapseAgent
from synapse_channel.gitclaim import AgentFactory, GitError, GitRunner, _default_git_runner
from synapse_channel.protocol import MessageType

HOOK_MARKER = "# synapse-git-hook"
"""Marker line that identifies a hook this tool wrote, so it is never clobbered."""

TRIGGER_HOOKS = {"commit": "post-commit", "merge": "post-merge"}
"""Maps an auto-release trigger to the git hook filename that fires it."""


def hooks_directory(*, runner: GitRunner = _default_git_runner) -> Path:
    """Return the repository's git hooks directory via ``git rev-parse``."""
    return Path(runner(["rev-parse", "--git-path", "hooks"]))


def _hook_script(trigger: str, *, uri: str, name: str, token_file: str | None) -> str:
    """Build the shell-script body of a hook that calls ``synapse git-release``.

    Every value baked into the script is shell-quoted, so a name, URI, or token
    path containing spaces or shell metacharacters can neither break the hook nor
    inject a command into it.
    """
    auth = f" --token-file {shlex.quote(token_file)}" if token_file else ""
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        f"synapse git-release --trigger {trigger} "
        f"--uri {shlex.quote(uri)} --name {shlex.quote(name)}{auth} || true\n"
    )


def install_hooks(
    *,
    uri: str,
    name: str,
    token_file: str | None = None,
    runner: GitRunner = _default_git_runner,
    hooks_dir: Path | None = None,
) -> list[str]:
    """Install ``post-commit`` and ``post-merge`` auto-release hooks.

    A hook this tool already wrote (carrying :data:`HOOK_MARKER`) is overwritten;
    a pre-existing hook from anything else is left untouched and reported, so a
    user's own hooks are never clobbered.

    Parameters
    ----------
    uri, name : str
        Hub URI and agent identity baked into the hook's ``git-release`` call.
    token_file : str or None, optional
        A token file passed through to ``git-release`` for a secured hub.
    runner : GitRunner, optional
        The git executor; injectable for testing.
    hooks_dir : Path or None, optional
        Override the hooks directory; resolved from git when ``None``.

    Returns
    -------
    list[str]
        One human-readable line per hook installed or skipped.
    """
    target = hooks_dir if hooks_dir is not None else hooks_directory(runner=runner)
    target.mkdir(parents=True, exist_ok=True)
    results: list[str] = []
    for trigger, filename in sorted(TRIGGER_HOOKS.items()):
        path = target / filename
        if path.exists() and HOOK_MARKER not in path.read_text(encoding="utf-8", errors="ignore"):
            results.append(f"skipped {filename}: a non-Synapse hook already exists")
            continue
        path.write_text(
            _hook_script(trigger, uri=uri, name=name, token_file=token_file), encoding="utf-8"
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        results.append(f"installed {filename} (releases claims set to auto-release on {trigger})")
    return results


def changed_files(trigger: str, *, runner: GitRunner = _default_git_runner) -> list[str]:
    """Return the files touched by the just-completed commit or merge.

    Parameters
    ----------
    trigger : str
        ``commit`` (files in ``HEAD``) or ``merge`` (the ``ORIG_HEAD..HEAD`` range).
    runner : GitRunner, optional
        The git executor; injectable for testing.

    Returns
    -------
    list[str]
        Repository-relative paths of the changed files.
    """
    if trigger == "merge":
        out = runner(["diff", "--name-only", "ORIG_HEAD", "HEAD"])
    else:
        out = runner(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"])
    return [line for line in out.splitlines() if line.strip()]


def _paths_overlap(claim_paths: list[str], changed: list[str]) -> bool:
    """Return whether a claim's declared paths intersect the changed files.

    An empty claim scope (the whole worktree) is touched by any change. A declared
    path matches a changed file it equals or is a directory prefix of.
    """
    if not claim_paths:
        return bool(changed)
    for raw in claim_paths:
        prefix = raw.rstrip("/")
        for changed_path in changed:
            if changed_path == prefix or changed_path.startswith(prefix + "/"):
                return True
    return False


async def run_git_release(
    *,
    uri: str,
    name: str,
    trigger: str,
    token: str | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    runner: GitRunner = _default_git_runner,
) -> int:
    """Release this agent's branch-scoped claims whose paths were committed/merged.

    A hook must never block a commit, so an unreachable hub is not an error here;
    only a git failure before the hub is contacted returns non-zero.

    Parameters
    ----------
    uri, name : str
        Hub URI and the releasing agent's identity.
    trigger : str
        ``commit`` or ``merge`` — only claims whose ``auto_release_on`` matches are
        released.
    token : str or None, optional
        Shared-secret token for a secured hub.
    agent_factory : AgentFactory, optional
        Factory for the hub client; injectable for testing.
    runner : GitRunner, optional
        The git executor; injectable for testing.

    Returns
    -------
    int
        ``0`` on a clean run (including an unreachable hub); ``1`` only when git
        fails before the hub is contacted.
    """
    try:
        changed = changed_files(trigger, runner=runner)
    except GitError as exc:
        print(f"git error: {exc}")
        return 1

    snapshots: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.STATE_SNAPSHOT:
            snapshots.append(data.get("snapshot", {}))

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=5.0):
            print(f"[{name}] Could not reach hub at {uri}.")
            return 0
        await agent.request_state()
        for _ in range(40):
            if snapshots:
                break
            await asyncio.sleep(0.05)
        released: list[str] = []
        claims = (snapshots[-1].get("active_claims") or []) if snapshots else []
        for claim in claims:
            git = claim.get("git")
            if claim.get("owner") != name or not git or git.get("auto_release_on") != trigger:
                continue
            if _paths_overlap([str(p) for p in (claim.get("paths") or [])], changed):
                task_id = str(claim.get("task_id"))
                await agent.release(task_id)
                released.append(task_id)
        if released:
            print(f"released on {trigger}: {', '.join(released)}")
        return 0
    finally:
        agent.running = False
        conn_task.cancel()
