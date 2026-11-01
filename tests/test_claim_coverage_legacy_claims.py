# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity-bearing targets against legacy claims, and target validation
"""Decide coverage when only the target carries a path identity, and refuse bad targets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.git.claim_coverage import (
    ClaimCoverageError,
    ClaimCoverageVerdict,
    decide_claim_coverage,
)


def _legacy_claim(worktree: Path, paths: list[str]) -> dict[str, Any]:
    """A claim written by a producer that records no path identity."""
    return {
        "task_id": "TASK",
        "owner": "seat/one",
        "status": "claimed",
        "worktree": str(worktree),
        "paths": paths,
        "git": {"branch": "main", "base": "main", "auto_release_on": "manual"},
    }


def _target(root: Path, *names: str) -> ClaimScopeIdentity:
    return ClaimScopeIdentity(
        worktree_path=root.as_posix(),
        case_sensitive=True,
        paths=tuple(CanonicalPathIdentity(name, name) for name in names),
    )


def _decide(root: Path, claims: list[dict[str, Any]], *names: str) -> ClaimCoverageVerdict:
    return decide_claim_coverage(
        {"active_claims": claims},
        identity="seat/one",
        root=root,
        branch="main",
        paths=names,
        path_identity=_target(root, *names),
    )


def test_legacy_claim_on_the_same_root_covers_an_identity_target(tmp_path: Path) -> None:
    """A literal claim and a whole-tree claim both authorize an identity-bearing target."""
    root = tmp_path.resolve()
    assert _decide(root, [_legacy_claim(root, ["owned.py"])], "owned.py").allowed
    assert _decide(root, [_legacy_claim(root, [])], "anything.py").allowed
    sibling = _decide(root, [_legacy_claim(root, ["owned.py"])], "other.py")
    assert sibling.missing_paths == ("other.py",)


def test_legacy_claim_on_another_root_does_not_cover(tmp_path: Path) -> None:
    """A legacy claim bound to a different worktree leaves the target unclaimed."""
    root = tmp_path.resolve()
    verdict = _decide(root, [_legacy_claim(root / "other-worktree", ["owned.py"])], "owned.py")
    assert verdict.missing_paths == ("owned.py",)
    assert not verdict.allowed


def test_legacy_claim_with_an_unresolvable_worktree_fails_closed(tmp_path: Path) -> None:
    """A self-referencing worktree link in a legacy claim is refused, not skipped."""
    root = tmp_path.resolve()
    loop = root / "loop"
    loop.symlink_to(loop)
    with pytest.raises(ClaimCoverageError, match="^Hub returned an invalid claim worktree\\.$"):
        _decide(root, [_legacy_claim(loop, ["owned.py"])], "owned.py")


def test_target_identity_must_match_and_name_each_path_once(tmp_path: Path) -> None:
    """A target identity for other paths, or a repeated target, is refused before matching."""
    root = tmp_path.resolve()
    with pytest.raises(ClaimCoverageError, match="^Claim coverage target scope identity is"):
        decide_claim_coverage(
            {"active_claims": []},
            identity="seat/one",
            root=root,
            branch="main",
            paths=("owned.py",),
            path_identity=_target(root, "other.py"),
        )
    with pytest.raises(ClaimCoverageError, match="^Claim coverage targets must be unique"):
        _decide(root, [], "owned.py", "owned.py")
