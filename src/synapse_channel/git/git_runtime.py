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
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from collections.abc import Callable

from synapse_channel.core.errors import SynapseError

GitRunner = Callable[[list[str]], str]
"""Runs a Git subcommand and returns stdout without terminal CR/LF characters."""


class GitError(SynapseError, RuntimeError):
    """A Git command failed, or Git is not available on the host."""

    code = "git"


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
