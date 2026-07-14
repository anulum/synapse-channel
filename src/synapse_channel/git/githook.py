# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — git hooks that auto-release branch-scoped claims
"""Git hook integration that auto-releases branch-scoped claims.

A companion to :mod:`synapse_channel.git.gitclaim`. ``synapse git-hook install``
writes ``post-commit`` and ``post-merge`` hooks that call ``synapse git-release``,
which releases this agent's branch-scoped claims whose declared paths were just
committed or merged. As everywhere in the git integration, the work is entirely
client-side: the hub only ever receives an ordinary release and never learns that
git was involved. Both the git executor and the file resolution are injectable so
the flow is unit-testable without a real repository.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import stat
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.gitclaim import AgentFactory, GitError, GitRunner, _default_git_runner
from synapse_channel.terminal_text import shell_command_arg, shell_long_option

HOOK_MARKER = "# synapse-git-hook"
"""Marker line that identifies a hook this tool wrote, so it is never clobbered."""

TRIGGER_HOOKS = {"commit": "post-commit", "merge": "post-merge"}
"""Maps an auto-release trigger to the git hook filename that fires it."""


def hooks_directory(*, runner: GitRunner = _default_git_runner) -> Path:
    """Return the repository's git hooks directory via ``git rev-parse``."""
    return Path(runner(["rev-parse", "--git-path", "hooks"]))


def _resolve_synapse_bin(explicit: str | None) -> str:
    """Resolve the ``synapse`` executable baked into a hook.

    An explicit path wins; otherwise the absolute path of ``synapse`` on the
    current ``PATH`` is used, so a hook is not vulnerable to a later ``PATH``
    hijack. When ``synapse`` cannot be found, the bare name is used as a fallback
    (resolved from ``PATH`` at hook time).
    """
    if explicit:
        return explicit
    found = shutil.which("synapse")
    return str(Path(found).resolve()) if found else "synapse"


def _hook_script(
    trigger: str, *, uri: str, name: str, token_file: str | None, synapse_bin: str
) -> str:
    """Build the shell-script body of a hook that calls ``synapse git-release``.

    The hook passes ``--resolve-identity`` so ``git-release`` first reads the seat
    identity, hub, and token file recorded for the *current* worktree by
    ``synapse git-init``. Because git worktrees share one hooks directory, that
    per-worktree resolution is what lets a single shared hook release each
    worktree's own claims instead of the installer's. The baked ``--uri`` /
    ``--name`` / ``--token-file`` remain as the fallback used wherever a worktree
    has no recorded identity, preserving the single-worktree behaviour.

    Every value baked into the script is shell-quoted, so a name, URI, token path,
    or executable path containing spaces or shell metacharacters can neither break
    the hook nor inject a command into it. ``synapse_bin`` is the resolved path to
    the ``synapse`` executable (an absolute path hardens the hook against a
    ``PATH`` hijack).
    """
    auth = f" {shell_long_option('--token-file', token_file)}" if token_file else ""
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        f"{shell_command_arg(synapse_bin)} git-release --resolve-identity "
        f"{shell_long_option('--trigger', trigger)} {shell_long_option('--uri', uri)} "
        f"{shell_long_option('--name', name)}{auth} || true\n"
    )


def install_hooks(
    *,
    uri: str,
    name: str,
    token_file: str | None = None,
    synapse_bin: str | None = None,
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
        Hub URI and agent identity baked into the hook's ``git-release`` call as
        the fallback; the installed hook prefers the identity recorded for the
        current worktree at run time (see :func:`_hook_script`).
    token_file : str or None, optional
        A token file passed through to ``git-release`` for a secured hub, used as
        the fallback when the current worktree records no token file.
    synapse_bin : str or None, optional
        Path to the ``synapse`` executable to invoke from the hook; when ``None``
        the absolute path of ``synapse`` on the current ``PATH`` is baked in, so
        the hook is not vulnerable to a later ``PATH`` hijack.
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
    binary = _resolve_synapse_bin(synapse_bin)
    results: list[str] = []
    for trigger, filename in sorted(TRIGGER_HOOKS.items()):
        path = target / filename
        if path.exists() and HOOK_MARKER not in path.read_text(encoding="utf-8", errors="ignore"):
            results.append(f"skipped {filename}: a non-Synapse hook already exists")
            continue
        path.write_text(
            _hook_script(trigger, uri=uri, name=name, token_file=token_file, synapse_bin=binary),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        results.append(f"installed {filename} (releases claims set to auto-release on {trigger})")
    return results


def hook_installed(
    trigger: str,
    *,
    runner: GitRunner = _default_git_runner,
    hooks_dir: Path | None = None,
) -> bool:
    """Return whether this tool's auto-release hook for ``trigger`` is installed.

    The hub never enacts ``auto_release_on`` — only the client-side git hook does —
    so this is what tells a caller whether a ``--auto-release-on commit/merge`` claim
    will actually be released automatically, or is waiting on a ``synapse git-hook``.

    Parameters
    ----------
    trigger : str
        ``commit`` or ``merge``; any other value returns ``False``.
    runner : GitRunner, optional
        The git executor; injectable for testing.
    hooks_dir : Path or None, optional
        Override the hooks directory; resolved from git when ``None``.

    Returns
    -------
    bool
        ``True`` only when the matching hook file exists and carries
        :data:`HOOK_MARKER` (so a user's unrelated hook of the same name is not
        mistaken for one of ours).
    """
    filename = TRIGGER_HOOKS.get(trigger)
    if filename is None:
        return False
    target = hooks_dir if hooks_dir is not None else hooks_directory(runner=runner)
    path = target / filename
    return path.exists() and HOOK_MARKER in path.read_text(encoding="utf-8", errors="ignore")


def _hook_synapse_bin(text: str) -> str | None:
    """Return the ``synapse`` executable a Synapse hook script invokes, or ``None``.

    Reads the resolved binary back out of the hook's ``git-release`` line (the
    first shell token), so a test can confirm the path a hook will actually run.
    """
    for line in text.splitlines():
        if "git-release" in line:
            tokens = shlex.split(line)
            return tokens[0] if tokens else None
    return None


def _binary_resolvable(synapse_bin: str | None) -> bool:
    """Return whether a hook's ``synapse`` executable can be found and run.

    An absolute path must be an executable file; a bare name must resolve on the
    current ``PATH``. ``None`` (no binary parsed) is never resolvable.
    """
    if not synapse_bin:
        return False
    candidate = Path(synapse_bin)
    if candidate.is_absolute():
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(synapse_bin) is not None


def check_hooks(
    *,
    runner: GitRunner = _default_git_runner,
    hooks_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Report the install state of each auto-release hook and its executable.

    The hub never enacts ``auto_release_on``; only the client-side hook does, and a
    hook that is missing — or that bakes in a ``synapse`` path which has since moved
    — fails silently at commit time. This surfaces that state so an operator can
    confirm a working setup with ``synapse git-hook test`` instead of discovering a
    no-op the next time a claim should have auto-released.

    Parameters
    ----------
    runner : GitRunner, optional
        The git executor; injectable for testing.
    hooks_dir : Path or None, optional
        Override the hooks directory; resolved from git when ``None``.

    Returns
    -------
    list[dict[str, Any]]
        One mapping per trigger (sorted): ``trigger``, ``filename``, ``installed``
        (carries :data:`HOOK_MARKER`), ``synapse_bin`` (the executable the hook
        invokes, or ``None``), and ``binary_ok`` (whether that executable resolves).
    """
    target = hooks_dir if hooks_dir is not None else hooks_directory(runner=runner)
    report: list[dict[str, Any]] = []
    for trigger, filename in sorted(TRIGGER_HOOKS.items()):
        path = target / filename
        text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        installed = HOOK_MARKER in text
        synapse_bin = _hook_synapse_bin(text) if installed else None
        report.append(
            {
                "trigger": trigger,
                "filename": filename,
                "installed": installed,
                "synapse_bin": synapse_bin,
                "binary_ok": _binary_resolvable(synapse_bin) if installed else False,
            }
        )
    return report


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
    ready_timeout: float = 5.0,
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
    ready_timeout : float, optional
        Maximum seconds to wait for the hook client to receive the hub welcome.

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
        if not await agent.wait_until_ready(timeout=ready_timeout):
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
