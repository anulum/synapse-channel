# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared semantic-claim enforcement projection
"""Project physical repository changes onto branch-bound semantic claim paths.

The hub stores semantic scopes as ordinary synthetic descendant paths. Runtime
enforcement still needs two local operations the hub cannot perform: select
claims for one exact worktree/branch and translate a proven Git diff into those
synthetic paths. This module owns those pure operations so provider guards,
staged checks, and auto-release cannot drift onto different interpretations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from synapse_channel.core.scoping import normalize_path
from synapse_channel.git.semantic_diff import SemanticDiffRecord
from synapse_channel.git.semantic_scope import parse_semantic_scope
from synapse_channel.path_resolution import resolve_weakly_fail_closed


class SemanticEnforcementError(RuntimeError):
    """Semantic claim state is malformed and cannot be enforced safely."""


def claim_paths_for_context(
    claim: Mapping[str, Any],
    *,
    root: Path,
    branch: str,
) -> tuple[str, ...] | None:
    """Return claim paths when ``claim`` belongs to ``root`` and ``branch``.

    Parameters
    ----------
    claim : Mapping[str, Any]
        One active-claim object from the authoritative hub snapshot.
    root : pathlib.Path
        Canonical local Git worktree root.
    branch : str
        Exact current branch.

    Returns
    -------
    tuple[str, ...] or None
        Validated paths for a matching claim, or ``None`` when the claim belongs
        to another context or has no filesystem-bound worktree.

    Raises
    ------
    SemanticEnforcementError
        If a potentially relevant claim has malformed worktree or path data.
    """
    worktree = claim.get("worktree")
    if worktree is None or (isinstance(worktree, str) and not worktree.strip()):
        return None
    if not isinstance(worktree, str):
        raise SemanticEnforcementError("Hub returned an invalid claim worktree.")

    git = claim.get("git")
    if not isinstance(git, dict) or git.get("branch") != branch:
        return None

    paths = claim.get("paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise SemanticEnforcementError("Hub returned malformed claim paths.")
    try:
        claimed_root = resolve_weakly_fail_closed(Path(worktree))
    except (OSError, RuntimeError, ValueError) as exc:
        raise SemanticEnforcementError("Hub returned an invalid claim worktree.") from exc
    if claimed_root != root:
        return None
    return tuple(paths)


def semantic_claim_covers_source(paths: Sequence[str], target: str) -> bool:
    """Return whether ``paths`` contains a semantic claim for physical ``target``.

    Parameters
    ----------
    paths : Sequence[str]
        Validated claim paths from one worktree and branch.
    target : str
        Physical repository-relative source path.

    Returns
    -------
    bool
        ``True`` when at least one canonical semantic scope names ``target``.
    """
    source = normalize_path(target)
    return source in semantic_sources_from_paths(paths)


def semantic_sources_from_paths(paths: Sequence[str]) -> tuple[str, ...]:
    """Return stable physical source paths encoded by semantic ``paths``.

    Parameters
    ----------
    paths : Sequence[str]
        Arbitrary ordinary or semantic claim paths.

    Returns
    -------
    tuple[str, ...]
        De-duplicated canonical source paths from valid semantic scopes.
    """
    sources = [
        parsed.source
        for parsed in (parse_semantic_scope(path) for path in paths)
        if parsed is not None
    ]
    return tuple(dict.fromkeys(sources))


def semantic_sources_for_context(
    snapshot: Mapping[str, Any],
    *,
    root: Path,
    branch: str,
    targets: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return semantic source files claimed in one exact Git context.

    Parameters
    ----------
    snapshot : Mapping[str, Any]
        Authoritative hub state containing ``active_claims``.
    root : pathlib.Path
        Canonical local worktree root.
    branch : str
        Exact current branch.
    targets : Sequence[str], optional
        Optional physical paths that bound the returned sources.

    Returns
    -------
    tuple[str, ...]
        Stable, de-duplicated repository-relative source paths.

    Raises
    ------
    SemanticEnforcementError
        If the snapshot or a matching contextual claim is malformed.
    """
    claims = snapshot.get("active_claims")
    if not isinstance(claims, list):
        raise SemanticEnforcementError("Hub state snapshot has no valid active_claims list.")
    canonical_targets = {normalize_path(target) for target in targets}
    sources: list[str] = []
    for item in claims:
        if not isinstance(item, dict):
            raise SemanticEnforcementError("Hub state snapshot contains a malformed claim.")
        paths = claim_paths_for_context(item, root=root, branch=branch)
        if paths is None:
            continue
        for source in semantic_sources_from_paths(paths):
            if canonical_targets and source not in canonical_targets:
                continue
            sources.append(source)
    return tuple(dict.fromkeys(sources))


def project_change_paths(
    physical_paths: Sequence[str],
    records: Sequence[SemanticDiffRecord],
) -> tuple[str, ...]:
    """Replace proven modified sources with their semantic diff claim paths.

    A source is replaced only when its record is explicitly narrowed. Missing,
    ambiguous, file-wide, add/delete/rename, or unsupported records leave the
    physical path intact, which requires a whole-file claim and therefore fails
    closed for a symbol-only owner.

    Parameters
    ----------
    physical_paths : Sequence[str]
        Physical paths requiring enforcement in stable diagnostic order.
    records : Sequence[SemanticDiffRecord]
        Conservative semantic evidence for some or all physical paths.

    Returns
    -------
    tuple[str, ...]
        Exact semantic paths for proven sources and physical paths otherwise.

    Raises
    ------
    SemanticEnforcementError
        If multiple narrowed records claim authority for the same source.
    """
    narrowed: dict[str, tuple[str, ...]] = {}
    for record in records:
        if not record.narrowed:
            continue
        source = normalize_path(record.source)
        scopes = tuple(parse_semantic_scope(path) for path in record.claim_paths)
        if not scopes or any(scope is None or scope.source != source for scope in scopes):
            raise SemanticEnforcementError(
                f"semantic diff returned invalid narrowed evidence: {record.source}"
            )
        if source in narrowed:
            raise SemanticEnforcementError(
                f"semantic diff returned duplicate source evidence: {record.source}"
            )
        narrowed[source] = record.claim_paths

    projected: list[str] = []
    for path in physical_paths:
        claim_paths = narrowed.get(normalize_path(path))
        if claim_paths is None:
            projected.append(path)
        else:
            projected.extend(claim_paths)
    return tuple(dict.fromkeys(projected))
