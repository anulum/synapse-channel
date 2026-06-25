# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — git-aware CLI commands (git-claim, git-hook, git-release, conflicts)
"""The git-aware ``synapse`` subcommands.

``git-claim`` scopes a claim to the current branch, ``git-hook`` installs the
hooks that auto-release such claims on commit/merge, ``git-release`` is the
hook-invoked release that resolves the changed files, and ``conflicts`` predicts
cross-branch path overlaps. All four resolve git state client-side and delegate
to the :mod:`synapse_channel.git` package, so they are grouped here as one
responsibility apart from the plain hub-client verbs; :func:`add_parsers`
registers their subparsers on the top-level CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.git.gitclaim import GitError, run_git_claim
from synapse_channel.git.gitconflict import run_conflicts
from synapse_channel.git.githook import check_hooks, install_hooks, run_git_release
from synapse_channel.git.gitinit import init_repo
from synapse_channel.service_setup import install_user_services, service_suggestions

AsyncGitCommand = Callable[..., Coroutine[Any, Any, int]]
"""Async git command callable used by the CLI dispatchers."""


def _cmd_git_claim(
    args: argparse.Namespace,
    *,
    claim_runner: AsyncGitCommand = run_git_claim,
    async_runner: Callable[[Coroutine[Any, Any, int]], int] = asyncio.run,
) -> int:
    """Dispatch the ``git-claim`` subcommand: a claim scoped to the current git branch.

    The branch is resolved client-side; the hub stores it as opaque metadata and
    never runs git itself.
    """
    return async_runner(
        claim_runner(
            uri=args.uri,
            name=args.name,
            task_id=args.task_id,
            paths=args.paths or [],
            base=args.base,
            auto_release_on=args.auto_release_on,
            token=args.token,
        )
    )


def _cmd_git_init(
    args: argparse.Namespace,
    *,
    repo_initializer: Callable[..., list[str]] = init_repo,
    service_installer: Callable[..., list[str]] = install_user_services,
    suggestion_builder: Callable[..., list[str]] = service_suggestions,
    cwd_name: str | None = None,
) -> int:
    """Set up claim-aware git in one step: install the hooks and write the scaffold.

    A thin wrapper over the existing git integration — it installs the same
    auto-release hooks as ``git-hook install`` and adds a ``.synapse/`` onboarding
    guide (branch convention + worktree workflow). Everything is client-side and
    idempotent; a re-run refreshes its own files and never clobbers a user's.
    """
    try:
        lines = repo_initializer(
            uri=args.uri,
            name=args.name,
            base_branch=args.base,
            token_file=getattr(args, "token_file", None),
            synapse_bin=args.synapse_bin,
        )
    except GitError as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return 1
    project = args.service_project or cwd_name or Path.cwd().name
    identity = args.service_identity or project
    if args.install_user_services or args.start_user_services:
        lines.extend(
            service_installer(
                project=project,
                identity=identity,
                synapse_bin=args.synapse_bin,
                start=args.start_user_services,
            )
        )
    else:
        lines.append("service setup available: run `synapse git-init --install-user-services`")
        lines.extend(
            suggestion_builder(project=project, identity=identity, synapse_bin=args.synapse_bin)
        )
    for line in lines:
        print(line)
    return 0


def _cmd_git_hook(
    args: argparse.Namespace,
    *,
    installer: Callable[..., list[str]] = install_hooks,
    hook_checker: Callable[..., list[dict[str, Any]]] = check_hooks,
) -> int:
    """Install or test the git hooks that auto-release branch-scoped claims.

    The hooks are written and inspected client-side and call ``synapse git-release``;
    the hub is never involved in installing, testing, or running them. ``test``
    reports the install state and binary reachability without touching anything.
    """
    if args.action == "test":
        return _git_hook_test(hook_checker=hook_checker)
    try:
        lines = installer(
            uri=args.uri,
            name=args.name,
            token_file=getattr(args, "token_file", None),
            synapse_bin=args.synapse_bin,
        )
    except GitError as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


def _git_hook_test(
    *,
    hook_checker: Callable[..., list[dict[str, Any]]] = check_hooks,
) -> int:
    """Report whether each auto-release hook is installed and its binary resolves.

    Returns ``0`` only when every hook is installed and the ``synapse`` executable it
    invokes resolves; otherwise it prints what is missing and returns ``1``, so a
    broken setup is caught here rather than silently no-opping at commit time.
    """
    try:
        report = hook_checker()
    except GitError as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return 1
    healthy = True
    for entry in report:
        if not entry["installed"]:
            print(f"missing: {entry['filename']} not installed (run `synapse git-hook install`)")
            healthy = False
        elif not entry["binary_ok"]:
            print(
                f"warning: {entry['filename']} installed but its synapse binary "
                f"{entry['synapse_bin']!r} is not resolvable"
            )
            healthy = False
        else:
            print(f"ok: {entry['filename']} installed -> {entry['synapse_bin']}")
    return 0 if healthy else 1


def _cmd_git_release(
    args: argparse.Namespace,
    *,
    release_runner: AsyncGitCommand = run_git_release,
    async_runner: Callable[[Coroutine[Any, Any, int]], int] = asyncio.run,
) -> int:
    """Release branch-scoped claims whose paths were just committed or merged.

    Invoked by the installed git hooks; resolves the changed files client-side and
    sends an ordinary release for each matching claim. It takes no task id — it
    auto-detects which claims to drop from the git diff — so a stray positional or
    a missing ``--trigger`` is answered with a hint at the right command rather than
    a bare argparse error (the trap that sent agents to the wrong verb).
    """
    if args.task_id is not None:
        print(
            f"git-release is hook-invoked and takes no task id (it auto-detects claims "
            f"from the git diff). For a manual drop use: "
            f"synapse release {args.task_id} --name {args.name}",
            file=sys.stderr,
        )
        return 2
    if args.trigger is None:
        print(
            "git-release needs --trigger {commit,merge}; it is normally invoked by the "
            "hooks `synapse git-hook` installs, not run by hand. For a manual drop use "
            "`synapse release <task> --name <you>`.",
            file=sys.stderr,
        )
        return 2
    return async_runner(
        release_runner(uri=args.uri, name=args.name, trigger=args.trigger, token=args.token)
    )


def _cmd_conflicts(
    args: argparse.Namespace,
    *,
    conflict_runner: AsyncGitCommand = run_conflicts,
    async_runner: Callable[[Coroutine[Any, Any, int]], int] = asyncio.run,
) -> int:
    """Predict merge conflicts between branch-scoped claims on different branches.

    Reads the hub's live claims and flags cross-branch path overlaps; ``--check-diff``
    refines the prediction against each branch's actual ``git diff``. All git work is
    client-side.
    """
    return async_runner(
        conflict_runner(uri=args.uri, name=args.name, token=args.token, check_diff=args.check_diff)
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the git-aware subparsers on the top-level CLI."""
    git_claim = subparsers.add_parser(
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

    git_init = subparsers.add_parser(
        "git-init",
        help="Set up claim-aware git in one step: install the hooks and write a .synapse/ guide.",
    )
    git_init.add_argument("--uri", default=DEFAULT_HUB_URI)
    git_init.add_argument("--name", default="USER")
    git_init.add_argument(
        "--base",
        default="main",
        help="Integration branch the convention branches off (default: main).",
    )
    git_init.add_argument(
        "--synapse-bin",
        default=None,
        help="Path to the synapse executable to invoke from the hooks; defaults to the "
        "absolute path resolved from PATH at install time (hardens against PATH hijack).",
    )
    git_init.add_argument(
        "--install-user-services",
        action="store_true",
        help="Also write systemd user units for hub, project presence, and wake arming.",
    )
    git_init.add_argument(
        "--start-user-services",
        action="store_true",
        help="Install units, daemon-reload, and enable/start hub, presence, and wake arming.",
    )
    git_init.add_argument(
        "--service-project",
        default=None,
        help="Project instance for generated services; defaults to the current directory name.",
    )
    git_init.add_argument(
        "--service-identity",
        default=None,
        help="Worker identity for wake arming; defaults to the service project.",
    )
    git_init.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    git_init.set_defaults(func=_cmd_git_init)

    git_hook = subparsers.add_parser(
        "git-hook",
        help="Install or test git hooks that auto-release branch-scoped claims on commit/merge.",
    )
    git_hook.add_argument(
        "action",
        choices=["install", "test"],
        help="install the hooks, or test that they are installed and their binary resolves.",
    )
    git_hook.add_argument("--uri", default=DEFAULT_HUB_URI)
    git_hook.add_argument("--name", default="USER")
    git_hook.add_argument(
        "--synapse-bin",
        default=None,
        help="Path to the synapse executable to invoke from the hook; defaults to the "
        "absolute path resolved from PATH at install time (hardens against PATH hijack).",
    )
    git_hook.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    git_hook.set_defaults(func=_cmd_git_hook)

    git_release = subparsers.add_parser(
        "git-release",
        help="Release branch-scoped claims whose paths were committed/merged (used by git hooks).",
    )
    git_release.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="(not accepted) git-release is hook-invoked and auto-detects claims; "
        "for a manual drop use `synapse release <task> --name <you>`.",
    )
    git_release.add_argument(
        "--trigger",
        choices=["commit", "merge"],
        default=None,
        help="Which auto-release trigger fired (required for the hook-invoked release).",
    )
    git_release.add_argument("--uri", default=DEFAULT_HUB_URI)
    git_release.add_argument("--name", default="USER")
    git_release.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    git_release.set_defaults(func=_cmd_git_release)

    conflicts = subparsers.add_parser(
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
