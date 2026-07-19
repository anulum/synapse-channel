# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bind ordinary file claims to the current Git worktree
"""Resolve optional Git identity for first-party ordinary path claims.

An ordinary claim remains usable outside Git, where the historical shared
worktree label is the only available namespace.  Inside a Git checkout,
however, every first-party file-scope producer must use the same canonical
worktree and additive path identity as ``git-claim``.  Otherwise two claim
dialects can hold the same physical file without contending.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.path_identity import PathIdentityError
from synapse_channel.git.gitclaim import GitError, GitRunner, _default_git_runner, resolve_repo
from synapse_channel.git.path_identity import resolve_claim_scope_identity


class OrdinaryClaimScopeError(SynapseError, RuntimeError):
    """An ordinary path claim could not be safely bound to its Git checkout."""

    code = "ordinary_claim_scope"


@dataclass(frozen=True)
class OrdinaryClaimScope:
    """Canonical local scope attached to an ordinary path claim.

    Parameters
    ----------
    worktree : str
        Strict OS-canonical Git worktree root.
    paths : tuple[str, ...]
        Canonical display paths aligned with ``path_identity``.
    path_identity : dict[str, object]
        Versioned comparison identity sent to the hub.
    """

    worktree: str
    paths: tuple[str, ...]
    path_identity: dict[str, object]


def _has_git_marker(start: Path | None = None) -> bool:
    """Return whether ``start`` is inside a conventional Git worktree.

    An unreadable current directory is treated as potentially Git-backed.  The
    caller uses this probe only after Git itself failed, so uncertainty must
    refuse the claim instead of selecting the alias-prone legacy namespace.
    ``lexists`` also detects a dangling ``.git`` indirection.
    """
    try:
        current = (start or Path.cwd()).resolve(strict=True)
    except (OSError, RuntimeError):
        return True
    return any(os.path.lexists(candidate / ".git") for candidate in (current, *current.parents))


def resolve_ordinary_claim_scope(
    paths: Sequence[str],
    *,
    runner: GitRunner = _default_git_runner,
) -> OrdinaryClaimScope | None:
    """Resolve a file claim against the current Git worktree when one exists.

    Parameters
    ----------
    paths : Sequence[str]
        Explicit repository-relative display paths. Empty input represents a
        caller-owned named mutex and is never rewritten.
    runner : GitRunner, optional
        Git command runner, injectable for tests.

    Returns
    -------
    OrdinaryClaimScope or None
        Canonical Git scope, or ``None`` when the process is genuinely outside
        a Git worktree and the legacy shared namespace must be retained.

    Raises
    ------
    OrdinaryClaimScopeError
        If Git or path identity fails inside a checkout. Such a failure is
        refused instead of silently downgrading to the alias-prone legacy
        namespace.
    """
    requested = tuple(paths)
    if not requested:
        return None
    try:
        raw_root = resolve_repo(runner=runner)
    except (GitError, OSError, RuntimeError, ValueError) as exc:
        if _has_git_marker():
            raise OrdinaryClaimScopeError(
                "ordinary file claim could not resolve the current Git worktree"
            ) from exc
        return None
    try:
        root, display_paths, path_identity = resolve_claim_scope_identity(
            Path(raw_root),
            requested,
            runner=runner,
        )
    except (GitError, PathIdentityError, OSError, RuntimeError, ValueError) as exc:
        raise OrdinaryClaimScopeError(
            "ordinary file claim could not establish a canonical path identity"
        ) from exc
    return OrdinaryClaimScope(
        worktree=root.as_posix(),
        paths=display_paths,
        path_identity=path_identity.as_dict(),
    )
