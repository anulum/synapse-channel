# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared Git execution types and subprocess boundary
"""Neutral Git execution primitives shared by claim and path-identity clients.

This module deliberately depends on neither :mod:`gitclaim` nor
:mod:`path_identity`. Both layers can therefore use one runner and error type
without forming the former deferred import cycle.

Two runners live here. The shared runner raises on any non-zero exit and
returns the output; the claim and path-identity clients use it. The bounded
runner keeps the exit code and the error output, takes optional standard input
and stops a call at its deadline; the code that writes refs uses it, because a
refused write is an answer to read, not an error.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from pathlib import Path

from synapse_channel.core.errors import SynapseError

GitRunner = Callable[[list[str]], str]
"""Runs a Git subcommand and returns stdout without terminal CR/LF characters."""

GIT_DEADLINE_SECONDS = 30.0
"""Seconds one bounded ``git`` call may take before it is stopped and refused."""

NO_GIT = "this needs a git executable on PATH, and none was found"
"""The bounded runner's refusal on a host without ``git``."""


class GitError(SynapseError, RuntimeError):
    """A Git command failed, or Git is not available on the host."""

    code = "git"


def git_executable() -> str:
    """Return the path of ``git`` for the bounded runner.

    Raises
    ------
    GitError
        With :data:`NO_GIT` when no ``git`` executable is on ``PATH``.
    """
    found = shutil.which("git")
    if found is None:
        raise GitError(NO_GIT)
    return found


def run_git(
    git: str, repository: Path, args: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one fixed ``git -C <repository> <args>`` within the deadline and return its result.

    The exit code and the error output are returned, never raised: a refused
    ref update is an answer the caller reads back.

    Parameters
    ----------
    git : str
        The ``git`` executable (see :func:`git_executable`).
    repository : pathlib.Path
        The working copy or bare repository to run in.
    args : list[str]
        The subcommand and its arguments, a fixed vector; no shell is involved.
    input_text : str or None, optional
        Text given to the command's standard input, for example the lines of an
        ``update-ref --stdin`` transaction; ``None`` gives it none.

    Raises
    ------
    GitError
        When the call does not finish within :data:`GIT_DEADLINE_SECONDS`.
    """
    try:
        return subprocess.run(  # nosec B603 # fixed git argument vector, no shell, bounded
            [git, "-C", str(repository), *args],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=GIT_DEADLINE_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"git {args[0]} did not finish within {GIT_DEADLINE_SECONDS:g} seconds"
        ) from exc


def _default_git_runner(args: list[str]) -> str:
    """Run ``git <args>`` and return stdout without terminal CR/LF characters.

    Parameters
    ----------
    args : list[str]
        The Git subcommand and its arguments (everything after ``git``).

    Returns
    -------
    str
        The command's standard output with terminal CR/LF characters removed.

    Raises
    ------
    GitError
        When Git is not installed or the command exits non-zero.
    """
    git = shutil.which("git")
    if git is None:
        raise GitError("git is not installed or not on PATH")
    try:
        result = subprocess.run(  # nosec B603
            [git, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=True,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"git {' '.join(args)} exited non-zero"
        raise GitError(detail) from exc
    stdout = result.stdout if result.stdout is not None else ""
    return stdout.rstrip("\r\n")
