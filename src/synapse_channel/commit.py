# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity-scoped, lease-guarded git commit workflow
"""Lease-guarded ``syn commit`` workflow.

``syn commit`` is the safe local shortcut for the most collision-prone operation
in a multi-agent repo: committing. It holds the existing Synapse git mutex while
staging only the requested paths and then commits only those pathspecs, leaving
unrelated staged or modified files outside the new commit.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from synapse_channel.cli_locking import _lock
from synapse_channel.client.agent import DEFAULT_HUB_URI


class IdentityLike(Protocol):
    """Resolved identity fields required by the commit workflow."""

    @property
    def project(self) -> str:
        """Resolved project name."""
        ...  # pragma: no cover

    @property
    def identity(self) -> str:
        """Resolved full identity name."""
        ...  # pragma: no cover


AsyncCommandRunner = Callable[[Sequence[str]], Awaitable[int]]
"""Coroutine callable that executes one argv vector and returns its exit code."""

LockRunner = Callable[..., Awaitable[int]]
"""Coroutine callable compatible with :func:`synapse_channel.cli_locking._lock`."""

AsyncRunner = Callable[[Coroutine[Any, Any, int]], int]
"""Synchronous runner for an async commit workflow."""

DEFAULT_WAIT_TIMEOUT = 120.0
"""Seconds ``syn commit`` waits for the project git lease by default."""


@dataclass(frozen=True)
class CommitRequest:
    """Parsed, validated request for one lease-guarded git commit.

    Attributes
    ----------
    name : str
        The resolved Synapse identity that owns the temporary git lease.
    lock_id : str
        The Synapse lease id, usually ``<project>:git``.
    paths : tuple[str, ...]
        Relative pathspecs to stage and commit.
    message : str
        Commit message passed to ``git commit -m``.
    wait_timeout : float
        Seconds to wait for the lease before failing.
    uri : str
        Hub WebSocket URI.
    token : str or None
        Optional shared-secret token for secured hubs.
    """

    name: str
    lock_id: str
    paths: tuple[str, ...]
    message: str
    wait_timeout: float
    uri: str = DEFAULT_HUB_URI
    token: str | None = None

    def stage_command(self) -> list[str]:
        """Return the path-limited ``git add`` command for this request."""
        return ["git", "add", "-A", "--", *self.paths]

    def commit_command(self) -> list[str]:
        """Return the path-limited ``git commit`` command for this request."""
        return ["git", "commit", "-m", self.message, "--", *self.paths]


def _is_safe_relative_path(path: str) -> bool:
    """Return whether ``path`` is a non-empty relative path outside ``.git``."""
    raw = path.strip()
    if not raw:
        return False
    normalised = raw.replace("\\", "/")
    parsed = PurePosixPath(normalised)
    return not parsed.is_absolute() and ".." not in parsed.parts and ".git" not in parsed.parts


def _build_parser() -> argparse.ArgumentParser:
    """Build the narrow parser for ``syn commit`` arguments."""
    parser = argparse.ArgumentParser(
        prog="syn commit",
        description="Acquire the project git lease and commit only the requested paths.",
    )
    parser.add_argument("paths", nargs="*", help="Relative paths to stage and commit.")
    parser.add_argument("-m", "--message", help="Commit message.")
    parser.add_argument(
        "--task-id",
        default=None,
        help="Lease id to hold while committing (default: <project>:git).",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT,
        help="Seconds to wait for the git lease (default: 120).",
    )
    parser.add_argument("--uri", default=DEFAULT_HUB_URI, help="Synapse hub WebSocket URI.")
    parser.add_argument("--token", default=None, help="Hub shared-secret token.")
    return parser


def build_request(
    identity: IdentityLike, argv: Sequence[str] | None = None
) -> CommitRequest | None:
    """Parse and validate a ``syn commit`` request.

    Parameters
    ----------
    identity : IdentityLike
        Resolved identity from the ergonomics layer.
    argv : Sequence[str] or None, optional
        Arguments after ``syn commit``.

    Returns
    -------
    CommitRequest or None
        A request ready to execute, or ``None`` after printing a focused usage
        error for invalid input.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv or []))
    except SystemExit:
        return None
    if not args.message:
        print("syn commit needs -m/--message.", file=sys.stderr)
        return None
    if not args.paths:
        print("syn commit needs at least one path.", file=sys.stderr)
        return None
    unsafe = [path for path in args.paths if not _is_safe_relative_path(path)]
    if unsafe:
        print(f"syn commit unsafe path: {unsafe[0]!r}", file=sys.stderr)
        return None
    return CommitRequest(
        name=identity.identity,
        lock_id=str(args.task_id or f"{identity.project}:git"),
        paths=tuple(args.paths),
        message=str(args.message),
        wait_timeout=float(args.wait_timeout),
        uri=str(args.uri),
        token=args.token,
    )


async def _run_command(command: Sequence[str]) -> int:
    """Run ``command`` without a shell and return its exit code."""
    proc = await asyncio.create_subprocess_exec(*command)
    return await proc.wait()


async def stage_then_commit(
    *,
    paths: Sequence[str],
    commit_command: Sequence[str],
    command_runner: AsyncCommandRunner = _run_command,
) -> int:
    """Stage ``paths`` and run ``commit_command`` only when staging succeeds.

    Parameters
    ----------
    paths : Sequence[str]
        Validated pathspecs to stage.
    commit_command : Sequence[str]
        The ``git commit`` argv to run after staging succeeds.
    command_runner : AsyncCommandRunner, optional
        Async command executor; injectable for tests.

    Returns
    -------
    int
        The first non-zero exit code, otherwise the commit command's exit code.
    """
    stage_status = await command_runner(["git", "add", "-A", "--", *paths])
    if stage_status != 0:
        return stage_status
    return await command_runner(list(commit_command))


async def run_request(
    request: CommitRequest,
    *,
    lock_runner: LockRunner = _lock,
    command_runner: AsyncCommandRunner = _run_command,
) -> int:
    """Execute ``request`` while holding its Synapse git lease."""

    async def locked_runner(command: list[str]) -> int:
        return await stage_then_commit(
            paths=request.paths,
            commit_command=command,
            command_runner=command_runner,
        )

    return await lock_runner(
        uri=request.uri,
        name=request.name,
        task_id=request.lock_id,
        command=request.commit_command(),
        paths=[],
        wait_timeout=request.wait_timeout,
        token=request.token,
        runner=locked_runner,
    )


def main(
    identity: IdentityLike,
    argv: Sequence[str] | None = None,
    *,
    lock_runner: LockRunner = _lock,
    async_runner: AsyncRunner = asyncio.run,
) -> int:
    """Parse and run ``syn commit`` for ``identity``.

    Returns ``2`` for local usage errors before the hub is contacted. Runtime
    results come from the lease runner and the underlying git commands.
    """
    request = build_request(identity, argv)
    if request is None:
        return 2
    return async_runner(run_request(request, lock_runner=lock_runner))
