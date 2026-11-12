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

Every call goes through the bounded runner of
:mod:`synapse_channel.git.git_runtime`: a fixed argument vector, no shell, a
deadline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from synapse_channel.git.git_runtime import run_git


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
        The ``git`` executable (see :func:`synapse_channel.git.git_runtime.git_executable`).

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
