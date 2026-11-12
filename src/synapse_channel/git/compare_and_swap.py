# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — move one Git ref from exactly one commit to another, and read it back
"""Publish a prepared commit with one compare-and-swap of a local ref.

``git update-ref <ref> <new> <expected>`` changes the ref only while it holds
exactly ``expected``. A plain fast-forward would also succeed from any ancestor
of the expected commit and so would not check the declared pre-state. After the
command, the ref is read back: the exit code alone is not the result.

Every call runs ``git`` with a fixed argument vector, never through a shell,
and with a deadline. A host without ``git`` is refused with words.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 # fixed git argument vectors only, never a shell
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.git.git_runtime import GitError

GIT_DEADLINE_SECONDS = 30.0
"""Seconds one ``git`` call may take before it is stopped and refused."""

NO_GIT = "this needs a git executable on PATH, and none was found"
"""The refusal on a host without ``git``."""


@dataclass(frozen=True)
class SwapResult:
    """What one compare-and-swap did, as read back from the repository.

    Attributes
    ----------
    applied : bool
        The command succeeded and the ref now holds the new commit.
    head : str or None
        The ref's value read after the command, or ``None`` when it does not exist.
    detail : str
        Git's own message on a refusal (at most 200 characters), else empty.
    """

    applied: bool
    head: str | None
    detail: str


def git_executable() -> str:
    """Return the path of ``git``.

    Raises
    ------
    GitError
        When no ``git`` executable is on ``PATH``.
    """
    found = shutil.which("git")
    if found is None:
        raise GitError(NO_GIT)
    return found


def run_git(git: str, repository: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one fixed ``git -C <repository> <args>`` with the deadline and return its result.

    Raises
    ------
    GitError
        When the call does not finish within :data:`GIT_DEADLINE_SECONDS`.
    """
    try:
        return subprocess.run(  # nosec B603 # fixed git argument vector, no shell, bounded
            [git, "-C", str(repository), *args],
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


def read_ref(repository: Path, ref: str, *, git: str) -> str | None:
    """Return the commit ``ref`` holds in ``repository``, or ``None`` when it does not exist."""
    completed = run_git(git, repository, ["rev-parse", "--verify", "--quiet", ref])
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def compare_and_swap(
    repository: Path, ref: str, *, new: str, expected: str, git: str
) -> SwapResult:
    """Move ``ref`` from exactly ``expected`` to ``new`` and read back what it holds.

    Parameters
    ----------
    repository : pathlib.Path
        The working copy or bare repository that holds ``ref``.
    ref : str
        The full ref name, for example ``refs/heads/main``.
    new, expected : str
        Full commit names: the candidate and the declared pre-state.
    git : str
        The ``git`` executable (see :func:`git_executable`).

    Returns
    -------
    SwapResult
        ``applied`` only when the command succeeded and the ref holds ``new``.
    """
    completed = run_git(git, repository, ["update-ref", ref, new, expected])
    head = read_ref(repository, ref, git=git)
    applied = completed.returncode == 0 and head == new
    detail = "" if applied else (completed.stderr.strip() or "the ref was not changed")[:200]
    return SwapResult(applied, head, detail)
