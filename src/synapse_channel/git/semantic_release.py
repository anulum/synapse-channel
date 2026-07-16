# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — context-bound semantic auto-release evidence
"""Select claims and resolve semantic evidence for non-blocking Git release hooks."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from synapse_channel.git.gitclaim import GitError, GitRunner
from synapse_channel.git.semantic_diff import SemanticDiffRecord, resolve_git_diff
from synapse_channel.git.semantic_enforcement import (
    SemanticEnforcementError,
    claim_paths_for_context,
)
from synapse_channel.path_resolution import resolve_weakly_fail_closed

SemanticReleaseResolver = Callable[
    [Path, str, Sequence[str]],
    tuple[SemanticDiffRecord, ...],
]
"""Resolve committed semantic change evidence for auto-release."""

ReleaseCandidate = tuple[Mapping[str, Any], tuple[str, ...]]
"""One contextual claim and its validated paths."""


def release_context(*, runner: GitRunner) -> tuple[Path, str]:
    """Return the canonical worktree and attached branch for a firing Git hook.

    Parameters
    ----------
    runner : GitRunner
        Git executor bound to the repository whose hook fired.

    Returns
    -------
    tuple[pathlib.Path, str]
        Canonical worktree root and exact attached branch.

    Raises
    ------
    GitError
        If Git returns an empty, detached, or invalid context.
    """
    root_text = runner(["rev-parse", "--show-toplevel"]).strip()
    branch = runner(["symbolic-ref", "--quiet", "--short", "HEAD"]).strip()
    if not root_text or not branch:
        raise GitError("git returned no release worktree or attached branch")
    try:
        root = resolve_weakly_fail_closed(Path(root_text))
    except (OSError, RuntimeError, ValueError) as exc:
        raise GitError("git returned an invalid release worktree") from exc
    return root, branch


def resolve_release_semantics(
    root: Path,
    trigger: str,
    paths: Sequence[str],
) -> tuple[SemanticDiffRecord, ...]:
    """Resolve a completed commit or merge against its pre-trigger revision.

    Parameters
    ----------
    root : pathlib.Path
        Exact worktree whose hook fired.
    trigger : str
        ``commit`` or ``merge``.
    paths : Sequence[str]
        Physical semantic source files requiring release evidence.

    Returns
    -------
    tuple[SemanticDiffRecord, ...]
        Conservative committed-diff evidence for the requested sources.
    """
    base = "ORIG_HEAD" if trigger == "merge" else "HEAD^"
    return resolve_git_diff(root, base=base, head="HEAD", paths=paths)


def release_candidates(
    claims: Sequence[object],
    *,
    name: str,
    trigger: str,
    root: Path,
    branch: str,
) -> tuple[ReleaseCandidate, ...]:
    """Return owner/trigger claims bound to the firing Git context.

    Malformed or mismatched claims are retained rather than released. The hook
    is non-blocking, so unsafe state is handled by omission, never by widening.

    Parameters
    ----------
    claims : Sequence[object]
        Active claims from the authoritative hub snapshot.
    name : str
        Exact owner resolved for the firing worktree.
    trigger : str
        ``commit`` or ``merge``.
    root : pathlib.Path
        Canonical worktree whose hook fired.
    branch : str
        Exact attached branch whose hook fired.

    Returns
    -------
    tuple[ReleaseCandidate, ...]
        Matching claims paired with validated claim paths.
    """
    candidates: list[ReleaseCandidate] = []
    for item in claims:
        if not isinstance(item, dict):
            continue
        git = item.get("git")
        if (
            item.get("owner") != name
            or not isinstance(git, dict)
            or git.get("auto_release_on") != trigger
        ):
            continue
        try:
            paths = claim_paths_for_context(item, root=root, branch=branch)
        except SemanticEnforcementError:
            continue
        if paths is not None:
            candidates.append((item, paths))
    return tuple(candidates)
