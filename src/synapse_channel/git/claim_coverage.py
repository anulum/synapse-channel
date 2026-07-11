# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure path-claim coverage decisions
"""Decide whether one identity owns editable claims for repository paths.

The matcher is shared by provider edit guards and commit-time staged-path
checks. It consumes one authoritative state snapshot and has no network, Git,
or mutation side effects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.lifecycle import TaskStatus
from synapse_channel.core.scoping import normalize_path

EDITABLE_STATUSES = frozenset({TaskStatus.CLAIMED, TaskStatus.WORKING})
"""Live task states in which the owner may mutate claimed files."""


class ClaimCoverageError(SynapseError, RuntimeError):
    """The authoritative claim snapshot cannot be evaluated safely."""

    code = "claim_coverage"


@dataclass(frozen=True)
class ClaimCoverageVerdict:
    """Stable path groups that explain a multi-path coverage decision."""

    missing_paths: tuple[str, ...] = ()
    ownership_mismatch_paths: tuple[str, ...] = ()
    non_editable_paths: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        """Return whether every requested path has one editable owner."""
        return not (self.missing_paths or self.ownership_mismatch_paths or self.non_editable_paths)


def claim_path_covers(scope: str, target: str) -> bool:
    """Return whether one literal claim path owns ``target``."""
    claimed = normalize_path(scope)
    relative = normalize_path(target)
    return claimed == "" or claimed == relative or relative.startswith(claimed + "/")


def _claim_covers_path(
    claim: Mapping[str, Any],
    *,
    root: Path,
    branch: str,
    target: str,
) -> bool:
    worktree = claim.get("worktree")
    if worktree is None or (isinstance(worktree, str) and not worktree.strip()):
        return False
    if not isinstance(worktree, str):
        raise ClaimCoverageError("Hub returned an invalid claim worktree.")

    git = claim.get("git")
    if not isinstance(git, dict) or git.get("branch") != branch:
        return False

    paths = claim.get("paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ClaimCoverageError("Hub returned malformed claim paths.")
    try:
        claimed_root = Path(worktree).resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ClaimCoverageError("Hub returned an invalid claim worktree.") from exc
    if claimed_root != root:
        return False
    return not paths or any(claim_path_covers(path, target) for path in paths)


def decide_claim_coverage(
    snapshot: Mapping[str, Any],
    *,
    identity: str,
    root: Path,
    branch: str,
    paths: Sequence[str],
) -> ClaimCoverageVerdict:
    """Classify repository paths by claim coverage for ``identity``.

    Parameters
    ----------
    snapshot : Mapping[str, Any]
        Authoritative hub state containing ``active_claims``.
    identity : str
        Exact claim owner required for every covering claim.
    root : pathlib.Path
        Canonical repository worktree root.
    branch : str
        Exact current branch.
    paths : Sequence[str]
        Repository-relative paths to verify, in diagnostic order.

    Returns
    -------
    ClaimCoverageVerdict
        Stable, de-duplicated failure groups; an empty verdict is allowed.

    Raises
    ------
    ClaimCoverageError
        If the snapshot or a relevant covering claim is malformed.
    """
    claims = snapshot.get("active_claims")
    if not isinstance(claims, list):
        raise ClaimCoverageError("Hub state snapshot has no valid active_claims list.")
    if not all(isinstance(path, str) for path in paths):
        raise ClaimCoverageError("Claim coverage targets must be path strings.")
    try:
        canonical_root = root.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ClaimCoverageError("Claim coverage worktree is invalid.") from exc

    typed_claims: list[Mapping[str, Any]] = []
    for item in claims:
        if not isinstance(item, dict):
            raise ClaimCoverageError("Hub state snapshot contains a malformed claim.")
        typed_claims.append(item)

    missing: list[str] = []
    ownership: list[str] = []
    non_editable: list[str] = []
    for target in dict.fromkeys(paths):
        covering = [
            claim
            for claim in typed_claims
            if _claim_covers_path(
                claim,
                root=canonical_root,
                branch=branch,
                target=target,
            )
        ]
        if not covering:
            missing.append(target)
            continue

        owners = [claim.get("owner") for claim in covering]
        if not all(isinstance(owner, str) and owner for owner in owners):
            raise ClaimCoverageError("Hub returned a malformed covering claim owner.")
        statuses = [claim.get("status") for claim in covering]
        if not all(isinstance(status, str) for status in statuses):
            raise ClaimCoverageError("Hub returned a malformed covering claim status.")
        if set(owners) != {identity}:
            ownership.append(target)
        elif any(status not in EDITABLE_STATUSES for status in statuses):
            non_editable.append(target)

    return ClaimCoverageVerdict(
        missing_paths=tuple(missing),
        ownership_mismatch_paths=tuple(ownership),
        non_editable_paths=tuple(non_editable),
    )
