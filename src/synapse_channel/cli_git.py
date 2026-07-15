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

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.git.gitclaim import GitError, run_git_claim
from synapse_channel.git.gitconflict import run_conflicts
from synapse_channel.git.githook import check_hooks, install_hooks, run_git_release
from synapse_channel.git.gitinit import init_repo
from synapse_channel.git.hook_release_identity import ReleaseIdentity, resolve_release_identity
from synapse_channel.git.semantic_claim_suggest import (
    render_draft_claim,
    render_human,
    render_json,
    suggest_paths,
)
from synapse_channel.service_setup import (
    default_synapse_bin,
    install_user_services,
    service_suggestions,
    validate_systemd_executable,
)
from synapse_channel.terminal_text import shell_command_arg, shell_long_option

AsyncGitCommand = Callable[..., Coroutine[Any, Any, int]]
"""Async git command callable used by the CLI dispatchers."""

SEMANTIC_SELECTOR_FIELDS = (
    ("module", "module"),
    ("symbol", "symbol"),
    ("api", "api"),
    ("source", "source"),
    ("test", "test"),
    ("generated", "generated"),
    ("migration", "migration"),
)
"""``git-claim`` argparse fields that map directly to semantic selectors."""


def _resolve_git_claim_task_id(args: argparse.Namespace) -> str | None:
    """Return the git-claim task id or print a focused usage error.

    ``git-claim`` historically accepted the task id only as a positional argument.
    The named ``--task-id`` form is now accepted for scripts and agents that build
    argv from structured fields. Supplying both forms is ambiguous and rejected so
    automation never has to guess which value won.
    """
    positional = str(args.task_id).strip() if args.task_id is not None else ""
    flagged = str(args.task_id_flag).strip() if args.task_id_flag is not None else ""
    if positional and flagged:
        print(
            "git-claim: use either TASK_ID or --task-id, not both.",
            file=sys.stderr,
        )
        return None
    task_id = positional or flagged
    if not task_id:
        print(
            "git-claim needs TASK_ID or --task-id TASK_ID.",
            file=sys.stderr,
        )
        return None
    return task_id


def _semantic_selectors_from_args(args: argparse.Namespace) -> tuple[str, ...]:
    """Return ``kind:value`` semantic selectors from git-claim argparse fields.

    The hub remains file-scope only. These selectors are resolved client-side by
    :func:`synapse_channel.git.gitclaim.run_git_claim` after the local git root is
    known, then sent as ordinary ``paths``.
    """
    selectors: list[str] = []
    for field, kind in SEMANTIC_SELECTOR_FIELDS:
        for value in getattr(args, field, None) or ():
            selectors.append(f"{kind}:{value}")
    return tuple(selectors)


def _cmd_git_claim_suggest(args: argparse.Namespace) -> int:
    """Suggest paths for a claim from a free-text intent.

    This is the client-side slice of the auto-claim semantic layer. It does not
    contact the hub and does not acquire a claim; it only prints ranked paths or
    a draft ``synapse git-claim`` command that the agent or operator can review.
    """
    intent = (args.intent or "").strip()
    if not intent:
        print("git-claim suggest needs --intent <description>.", file=sys.stderr)
        return 2
    repo_root = Path.cwd()
    try:
        suggestions = suggest_paths(
            repo_root,
            intent,
            limit=max(1, int(args.suggest_limit)),
        )
    except (OSError, ValueError) as exc:
        print(f"semantic suggestion error: {exc}", file=sys.stderr)
        return 1
    if args.suggest_draft:
        print(
            render_draft_claim(
                suggestions,
                task_id=args.suggest_draft_task_id or "TASK-001",
                name=args.name,
                base=args.base,
            )
        )
    elif args.suggest_json:
        print(render_json(suggestions))
    else:
        print(render_human(suggestions))
    return 0


def _cmd_git_claim(
    args: argparse.Namespace,
    *,
    claim_runner: AsyncGitCommand = run_git_claim,
    async_runner: Callable[[Coroutine[Any, Any, int]], int] = asyncio.run,
) -> int:
    """Dispatch the ``git-claim`` subcommand: a claim scoped to the current git branch.

    The branch is resolved client-side; the hub stores it as opaque metadata and
    never runs git itself. The task id ``suggest`` combined with ``--intent``
    switches to intent-based path suggestion instead of claiming, so the existing
    top-level parser stays backward-compatible. A real task genuinely named
    ``suggest`` is still claimable — pass it without ``--intent`` (e.g. with
    ``--paths``) and it is claimed like any other id.
    """
    task_id = _resolve_git_claim_task_id(args)
    if task_id is None:
        return 2
    if task_id == "suggest" and args.intent:
        return _cmd_git_claim_suggest(args)
    return async_runner(
        claim_runner(
            uri=args.uri,
            name=args.name,
            task_id=task_id,
            paths=args.paths or [],
            base=args.base,
            auto_release_on=args.auto_release_on,
            token=args.token,
            semantic_selectors=_semantic_selectors_from_args(args),
            semantic_diff_base=getattr(args, "semantic_diff_base", None),
            semantic_diff_head=getattr(args, "semantic_diff_head", None),
            semantic_diff_paths=tuple(getattr(args, "semantic_diff_path", None) or ()),
            semantic_evidence_json=args.semantic_evidence_json,
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
    service_install_requested = args.install_user_services or args.start_user_services
    effective_synapse_bin = args.synapse_bin
    if service_install_requested:
        if effective_synapse_bin is None:
            effective_synapse_bin = default_synapse_bin()
        try:
            validate_systemd_executable(effective_synapse_bin)
        except ValueError as exc:
            print(f"git-init: {exc}", file=sys.stderr)
            return 2
    try:
        lines = repo_initializer(
            uri=args.uri,
            name=args.name,
            base_branch=args.base,
            token_file=getattr(args, "token_file", None),
            synapse_bin=effective_synapse_bin,
        )
    except GitError as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return 1
    project = args.service_project or cwd_name or Path.cwd().name
    identity = args.service_identity or project
    if service_install_requested:
        try:
            service_lines = service_installer(
                project=project,
                identity=identity,
                synapse_bin=effective_synapse_bin,
                start=args.start_user_services,
            )
        except ValueError as exc:
            print(f"git-init: {exc}", file=sys.stderr)
            return 2
        lines.extend(service_lines)
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
    identity_resolver: Callable[..., ReleaseIdentity | None] = resolve_release_identity,
    async_runner: Callable[[Coroutine[Any, Any, int]], int] = asyncio.run,
) -> int:
    """Release branch-scoped claims whose paths were just committed or merged.

    Invoked by the installed git hooks; resolves the changed files client-side and
    sends an ordinary release for each matching claim. It takes no task id — it
    auto-detects which claims to drop from the git diff — so a stray positional or
    a missing ``--trigger`` is answered with a hint at the right command rather than
    a bare argparse error (the trap that sent agents to the wrong verb).

    With ``--resolve-identity`` (the form the installed hooks use) the releasing
    seat, hub, and token come from the current worktree's git config, so one shared
    hook releases each worktree's own claims. When that worktree has no recorded
    identity the baked ``--name`` / ``--uri`` / ``--token`` are used unchanged.
    """
    if args.task_id is not None:
        release_command = (
            f"synapse release {shell_long_option('--name', args.name)} -- "
            f"{shell_command_arg(args.task_id)}"
        )
        print(
            f"git-release is hook-invoked and takes no task id (it auto-detects claims "
            f"from the git diff). For a manual drop use: {release_command}",
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
    uri, name, token = args.uri, args.name, args.token
    if getattr(args, "resolve_identity", False):
        resolved = identity_resolver()
        if resolved is not None:
            uri, name, token = resolved.uri, resolved.name, resolved.token
    return async_runner(release_runner(uri=uri, name=name, trigger=args.trigger, token=token))


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
    git_claim.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="Task id to claim. Use --task-id instead when building argv from structured fields.",
    )
    git_claim.add_argument(
        "--task-id",
        dest="task_id_flag",
        default=None,
        help="Task id to claim; equivalent to the positional TASK_ID. Do not pass both.",
    )
    git_claim.add_argument(
        "--paths",
        action="append",
        default=None,
        help="File-scope path the claim intends to touch (repeatable).",
    )
    git_claim.add_argument(
        "--module",
        action="append",
        default=None,
        help="Resolve an importable module to ordinary claim paths before claiming.",
    )
    git_claim.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="Resolve a fully qualified public symbol to ordinary claim paths.",
    )
    git_claim.add_argument(
        "--api",
        action="append",
        default=None,
        help="Resolve a fully qualified public API object to ordinary claim paths.",
    )
    git_claim.add_argument(
        "--source",
        action="append",
        default=None,
        help="Resolve a source path to its source, owning tests, and generated outputs.",
    )
    git_claim.add_argument(
        "--test",
        action="append",
        default=None,
        help="Resolve a test path to the source paths it likely owns.",
    )
    git_claim.add_argument(
        "--generated",
        action="append",
        default=None,
        help="Resolve a generated output path into a generated-output claim path.",
    )
    git_claim.add_argument(
        "--migration",
        action="append",
        default=None,
        help="Resolve a migration path into a migration claim path.",
    )
    git_claim.add_argument(
        "--semantic-evidence-json",
        default=None,
        help=(
            "Write receipt-ready semantic selector evidence JSON after resolving "
            "semantic claim flags. Relative paths are written under the git root."
        ),
    )
    git_claim.add_argument(
        "--diff-base",
        dest="semantic_diff_base",
        default=None,
        help=(
            "Infer conservative function scopes from this Git base versus the working tree, "
            "using the optional semantic extra."
        ),
    )
    git_claim.add_argument(
        "--diff-head",
        dest="semantic_diff_head",
        default=None,
        help="Optional committed head for --diff-base; omit for working-tree changes.",
    )
    git_claim.add_argument(
        "--diff-path",
        dest="semantic_diff_path",
        action="append",
        default=None,
        help="Limit tree-sitter diff inference to this repository-relative path; repeatable.",
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
    git_claim.add_argument(
        "--intent",
        default=None,
        help=(
            "Free-text intent for `synapse git-claim suggest --intent ...`. "
            "Ignored for ordinary claims."
        ),
    )
    git_claim.add_argument(
        "--limit",
        dest="suggest_limit",
        type=int,
        default=10,
        help="Maximum suggestions for `git-claim suggest` (default: 10).",
    )
    git_claim.add_argument(
        "--draft",
        dest="suggest_draft",
        action="store_true",
        help="For `git-claim suggest`, emit a draft `synapse git-claim` command.",
    )
    git_claim.add_argument(
        "--draft-task-id",
        dest="suggest_draft_task_id",
        default="TASK-001",
        help="Task id used in `--draft` output (default: TASK-001).",
    )
    git_claim.add_argument(
        "--json",
        dest="suggest_json",
        action="store_true",
        help="For `git-claim suggest`, emit JSON instead of human-readable text.",
    )
    git_claim.add_argument("--uri", default=default_hub_uri())
    git_claim.add_argument("--name", default="USER")
    git_claim.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    git_claim.set_defaults(func=_cmd_git_claim)

    git_init = subparsers.add_parser(
        "git-init",
        help="Set up claim-aware git in one step: install the hooks and write a .synapse/ guide.",
    )
    git_init.add_argument("--uri", default=default_hub_uri())
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
    git_hook.add_argument("--uri", default=default_hub_uri())
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
    git_release.add_argument(
        "--resolve-identity",
        action="store_true",
        help="Release as the seat recorded for the current worktree by `synapse git-init` "
        "(its synapse.identity/uri/tokenFile), falling back to --name/--uri. The installed "
        "hooks pass this so one shared hook releases each worktree's own claims.",
    )
    git_release.add_argument("--uri", default=default_hub_uri())
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
    conflicts.add_argument("--uri", default=default_hub_uri())
    conflicts.add_argument("--name", default="USER")
    conflicts.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    conflicts.set_defaults(func=_cmd_conflicts)
