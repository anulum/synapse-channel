# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Git-scoped MCP claim resolution
"""Resolve fail-closed Git claim metadata for the MCP coordination face."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.scoping import normalize_paths
from synapse_channel.core.state import GitContext
from synapse_channel.core.state_models import AUTO_RELEASE_MODES
from synapse_channel.git.gitclaim import (
    GitError,
    GitRunner,
    _default_git_runner,
    resolve_branch,
    resolve_repo,
)
from synapse_channel.git.path_identity import resolve_claim_scope_identity


class McpGitClaimError(SynapseError, RuntimeError):
    """The MCP caller requested an unsafe or unresolved Git claim scope."""

    code = "mcp_git_claim"


@dataclass(frozen=True)
class McpGitClaimScope:
    """Canonical local Git metadata attached to one MCP claim request.

    ``worktree`` and ``paths`` are the human-readable claim fields;
    ``path_identity`` is the aligned versioned comparison schema; and ``git``
    records branch, base, and release policy without asking the hub to inspect
    the repository.
    """

    worktree: str
    paths: tuple[str, ...]
    path_identity: dict[str, object]
    git: dict[str, str]


def _claim_paths(paths: Sequence[str] | None, *, whole_worktree: bool) -> tuple[str, ...]:
    """Validate explicit MCP path intent without silently widening its scope."""
    requested = tuple(paths or ())
    if whole_worktree:
        if requested:
            raise McpGitClaimError("MCP Git claim must use paths or whole_worktree, not both.")
        return ()
    if not requested:
        raise McpGitClaimError(
            "MCP Git claim needs at least one path unless whole_worktree is explicitly true."
        )
    if any(not isinstance(path, str) for path in requested):
        raise McpGitClaimError("MCP Git claim paths must be strings.")
    normalised = normalize_paths(requested)
    if not normalised or "" in normalised:
        raise McpGitClaimError(
            "MCP Git claim paths must be bounded repository-relative paths without traversal."
        )
    if normalised != requested:
        raise McpGitClaimError(
            "MCP Git claim paths must already be unique canonical display paths."
        )
    return normalised


def resolve_mcp_git_claim_scope(
    paths: Sequence[str] | None,
    *,
    base: str,
    auto_release_on: str,
    whole_worktree: bool = False,
    runner: GitRunner = _default_git_runner,
) -> McpGitClaimScope:
    """Resolve the current worktree and branch for one MCP Git claim.

    Parameters
    ----------
    paths : Sequence[str] or None
        Repository-relative files or directories. Empty input is refused unless
        ``whole_worktree`` is explicitly true.
    base : str
        Intended integration branch recorded on the claim.
    auto_release_on : str
        Client-side release trigger: ``manual``, ``commit``, or ``merge``.
    whole_worktree : bool, optional
        Explicitly request an unbounded worktree claim. Defaults to ``False``.
    runner : GitRunner, optional
        Git command runner used to resolve the local repository.

    Returns
    -------
    McpGitClaimScope
        Canonical worktree, bounded paths, and opaque Git metadata.

    Raises
    ------
    McpGitClaimError
        If intent is ambiguous, a field is unsafe, or Git cannot resolve the
        current repository and branch.
    """
    base_name = base.strip()
    if not base_name or not base_name.isprintable():
        raise McpGitClaimError("MCP Git claim base branch must be printable and non-empty.")
    if auto_release_on not in AUTO_RELEASE_MODES:
        raise McpGitClaimError("MCP Git claim auto_release_on must be manual, commit, or merge.")
    claim_paths = _claim_paths(paths, whole_worktree=whole_worktree)
    try:
        branch = resolve_branch(runner=runner)
        raw_root = resolve_repo(runner=runner)
        root, claim_paths, path_identity = resolve_claim_scope_identity(
            Path(raw_root),
            claim_paths,
            runner=runner,
        )
    except (GitError, OSError, RuntimeError, ValueError) as exc:
        raise McpGitClaimError("MCP Git claim could not resolve the current worktree.") from exc
    if not branch.strip() or not branch.isprintable() or not root.is_dir():
        raise McpGitClaimError("MCP Git claim resolved invalid repository metadata.")
    context = GitContext(
        branch=branch.strip(),
        base=base_name,
        auto_release_on=auto_release_on,
    )
    return McpGitClaimScope(
        worktree=root.as_posix(),
        paths=claim_paths,
        path_identity=path_identity.as_dict(),
        git=context.as_dict(),
    )
