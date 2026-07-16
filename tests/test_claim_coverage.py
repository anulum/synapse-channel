# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared claim-coverage decision regressions

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.errors import error_code
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.git.claim_coverage import (
    ClaimCoverageError,
    claim_path_covers,
    decide_claim_coverage,
)
from synapse_channel.git.semantic_scope import semantic_scope_path


def _claim(
    root: Path,
    *,
    owner: str = "seat/one",
    paths: list[str] | None = None,
    branch: str = "main",
    status: str = "claimed",
) -> dict[str, Any]:
    return {
        "task_id": "TASK",
        "owner": owner,
        "status": status,
        "worktree": str(root),
        "paths": ["src"] if paths is None else paths,
        "git": {"branch": branch, "base": "main", "auto_release_on": "manual"},
    }


def test_literal_claim_path_coverage_is_directional_and_normalised() -> None:
    assert claim_path_covers("src", "src/package/module.py")
    assert claim_path_covers("src/package/module.py", "src/package/module.py")
    assert claim_path_covers("", "any/file.py")
    assert claim_path_covers("./src//package", "src/package/module.py")
    assert not claim_path_covers("src/package/module.py", "src/package")
    assert not claim_path_covers("src/one", "src/other.py")


def test_multi_path_verdict_preserves_stable_failure_groups(tmp_path: Path) -> None:
    snapshot = {
        "active_claims": [
            _claim(tmp_path, paths=["src/owned.py"]),
            _claim(tmp_path, owner="seat/two", paths=["src/foreign.py"]),
            _claim(tmp_path, paths=["src/paused.py"], status="input_required"),
        ]
    }
    verdict = decide_claim_coverage(
        snapshot,
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=(
            "src/missing.py",
            "src/owned.py",
            "src/foreign.py",
            "src/paused.py",
            "src/missing.py",
        ),
    )
    assert verdict.missing_paths == ("src/missing.py",)
    assert verdict.ownership_mismatch_paths == ("src/foreign.py",)
    assert verdict.non_editable_paths == ("src/paused.py",)
    assert not verdict.allowed


@pytest.mark.parametrize("status", ["claimed", "working"])
def test_exact_subtree_and_whole_tree_claims_allow_editable_owner(
    tmp_path: Path, status: str
) -> None:
    for paths in (["src/package/module.py"], ["src"], []):
        verdict = decide_claim_coverage(
            {"active_claims": [_claim(tmp_path, paths=paths, status=status)]},
            identity="seat/one",
            root=tmp_path,
            branch="main",
            paths=("src/package/module.py",),
        )
        assert verdict.allowed


def test_wrong_worktree_branch_and_sibling_do_not_cover(tmp_path: Path) -> None:
    snapshot = {
        "active_claims": [
            _claim(tmp_path / "other", paths=[]),
            _claim(tmp_path, branch="other", paths=[]),
            _claim(tmp_path, paths=["src/sibling"]),
            {"task_id": "GLOBAL", "worktree": "", "paths": [], "git": None},
        ]
    }
    verdict = decide_claim_coverage(
        snapshot,
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=("src/target.py",),
    )
    assert verdict.missing_paths == ("src/target.py",)


def test_competing_covering_claim_is_ambiguous(tmp_path: Path) -> None:
    verdict = decide_claim_coverage(
        {
            "active_claims": [
                _claim(tmp_path, owner="seat/one"),
                _claim(tmp_path, owner="seat/two"),
            ]
        },
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=("src/module.py",),
    )
    assert verdict.ownership_mismatch_paths == ("src/module.py",)


def test_semantic_source_fallback_is_explicit_owner_exclusive_and_editable(
    tmp_path: Path,
) -> None:
    source = "src/module.py"
    owner_scope = semantic_scope_path(source, "owned")
    other_scope = semantic_scope_path(source, "other")
    snapshot = {"active_claims": [_claim(tmp_path, paths=[owner_scope])]}

    literal = decide_claim_coverage(
        snapshot,
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=(source,),
    )
    assert literal.missing_paths == (source,)

    precise = decide_claim_coverage(
        snapshot,
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=(source,),
        allow_semantic_source=True,
    )
    assert precise.allowed

    competing = decide_claim_coverage(
        {
            "active_claims": [
                _claim(tmp_path, paths=[owner_scope]),
                _claim(tmp_path, owner="seat/two", paths=[other_scope]),
            ]
        },
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=(source,),
        allow_semantic_source=True,
    )
    assert competing.ownership_mismatch_paths == (source,)

    paused = decide_claim_coverage(
        {"active_claims": [_claim(tmp_path, paths=[owner_scope], status="input_required")]},
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=(source,),
        allow_semantic_source=True,
    )
    assert paused.non_editable_paths == (source,)


def test_canonical_object_alias_does_not_widen_edit_authorization(tmp_path: Path) -> None:
    claim_identity = ClaimScopeIdentity(
        worktree_path=tmp_path.as_posix(),
        worktree_object_id="root:1",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("owned.py", "owned.py", "1:2"),),
    )
    target_identity = ClaimScopeIdentity(
        worktree_path=tmp_path.as_posix(),
        worktree_object_id="root:1",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("alias.py", "alias.py", "1:2"),),
    )
    claim = _claim(tmp_path, paths=["owned.py"])
    claim["path_identity"] = claim_identity.as_dict()

    verdict = decide_claim_coverage(
        {"active_claims": [claim]},
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=("alias.py",),
        path_identity=target_identity,
    )

    assert verdict.missing_paths == ("alias.py",)


def test_canonical_filesystem_alias_does_not_widen_edit_authorization(tmp_path: Path) -> None:
    """Unattested client alias evidence is denial-only, never an edit capability."""
    claim_identity = ClaimScopeIdentity(
        worktree_path=tmp_path.as_posix(),
        case_sensitive=True,
        paths=(CanonicalPathIdentity("harmless.py", "victim.py"),),
    )
    target_identity = ClaimScopeIdentity(
        worktree_path=tmp_path.as_posix(),
        case_sensitive=True,
        paths=(CanonicalPathIdentity("victim.py", "victim.py"),),
    )
    claim = _claim(tmp_path, paths=["harmless.py"])
    claim["path_identity"] = claim_identity.as_dict()

    verdict = decide_claim_coverage(
        {"active_claims": [claim]},
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=("victim.py",),
        path_identity=target_identity,
    )
    assert verdict.missing_paths == ("victim.py",)


def test_canonical_coverage_rejects_display_worktree_forgery(tmp_path: Path) -> None:
    """A snapshot cannot authorize via an identity detached from its display root."""
    identity = ClaimScopeIdentity(
        worktree_path=tmp_path.as_posix(),
        case_sensitive=True,
        paths=(CanonicalPathIdentity("owned.py", "owned.py"),),
    )
    claim = _claim(tmp_path / "different", paths=["owned.py"])
    claim["path_identity"] = identity.as_dict()
    with pytest.raises(ClaimCoverageError, match="scope identity"):
        decide_claim_coverage(
            {"active_claims": [claim]},
            identity="seat/one",
            root=tmp_path,
            branch="main",
            paths=("owned.py",),
            path_identity=identity,
        )


@pytest.mark.parametrize("alias_kind", ["trailing-space", "backslash"])
def test_canonical_coverage_keeps_legal_posix_roots_distinct(
    tmp_path: Path, alias_kind: str
) -> None:
    """Lossy display normalization cannot merge distinct POSIX repositories."""
    if alias_kind == "backslash" and __import__("os").name == "nt":
        pytest.skip("a backslash is a separator on Windows")
    claimed_root = tmp_path / "repo"
    target_root = (
        tmp_path / "repo " if alias_kind == "trailing-space" else tmp_path / "repo\\nested"
    )
    claimed_root.mkdir()
    target_root.mkdir()
    claim_identity = ClaimScopeIdentity(
        worktree_path=claimed_root.as_posix(),
        case_sensitive=True,
        paths=(CanonicalPathIdentity("victim.py", "victim.py"),),
    )
    target_identity = ClaimScopeIdentity(
        worktree_path=target_root.as_posix(),
        case_sensitive=True,
        paths=(CanonicalPathIdentity("victim.py", "victim.py"),),
    )
    claim = _claim(claimed_root, paths=["victim.py"])
    claim["path_identity"] = claim_identity.as_dict()

    verdict = decide_claim_coverage(
        {"active_claims": [claim]},
        identity="seat/one",
        root=target_root,
        branch="main",
        paths=("victim.py",),
        path_identity=target_identity,
    )
    assert verdict.missing_paths == ("victim.py",)


def test_canonical_coverage_denies_conflicting_case_policies(tmp_path: Path) -> None:
    """Conservative conflict folding must not widen directional authorization."""
    insensitive = ClaimScopeIdentity(
        worktree_path=tmp_path.as_posix().casefold(),
        case_sensitive=False,
        paths=(CanonicalPathIdentity("caseprobe.py", "caseprobe.py"),),
    )
    sensitive = ClaimScopeIdentity(
        worktree_path=tmp_path.as_posix(),
        case_sensitive=True,
        paths=(CanonicalPathIdentity("CASEPROBE.py", "CASEPROBE.py"),),
    )
    claim = _claim(tmp_path, paths=["caseprobe.py"])
    claim["path_identity"] = insensitive.as_dict()

    verdict = decide_claim_coverage(
        {"active_claims": [claim]},
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=("CASEPROBE.py",),
        path_identity=sensitive,
    )
    assert verdict.missing_paths == ("CASEPROBE.py",)


@pytest.mark.parametrize(
    ("target_namespace", "target_root_object"),
    [("host:2", "root:1"), ("host:1", "root:2")],
)
def test_canonical_coverage_rejects_stale_or_remote_same_path_root(
    tmp_path: Path, target_namespace: str, target_root_object: str
) -> None:
    """A same-spelled root is not an authorization match without provenance."""
    claim_identity = ClaimScopeIdentity(
        worktree_path=tmp_path.as_posix(),
        worktree_object_id="root:1",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("owned.py", "owned.py"),),
    )
    target_identity = ClaimScopeIdentity(
        worktree_path=tmp_path.as_posix(),
        worktree_object_id=target_root_object,
        filesystem_namespace=target_namespace,
        case_sensitive=True,
        paths=(CanonicalPathIdentity("owned.py", "owned.py"),),
    )
    claim = _claim(tmp_path, paths=["owned.py"])
    claim["path_identity"] = claim_identity.as_dict()
    verdict = decide_claim_coverage(
        {"active_claims": [claim]},
        identity="seat/one",
        root=tmp_path,
        branch="main",
        paths=("owned.py",),
        path_identity=target_identity,
    )
    assert verdict.missing_paths == ("owned.py",)


def test_canonical_coverage_rejects_present_invalid_identity(tmp_path: Path) -> None:
    claim = _claim(tmp_path, paths=["owned.py"])
    claim["path_identity"] = "invalid"
    with pytest.raises(ClaimCoverageError, match="malformed claim path identity"):
        decide_claim_coverage(
            {"active_claims": [claim]},
            identity="seat/one",
            root=tmp_path,
            branch="main",
            paths=("owned.py",),
        )


@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        ({"active_claims": "wrong"}, "active_claims"),
        ({"active_claims": ["wrong"]}, "malformed claim"),
    ],
)
def test_malformed_snapshot_fails_closed(
    tmp_path: Path, snapshot: dict[str, Any], message: str
) -> None:
    with pytest.raises(ClaimCoverageError, match=message):
        decide_claim_coverage(
            snapshot,
            identity="seat/one",
            root=tmp_path,
            branch="main",
            paths=("src/module.py",),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("paths", "src", "claim paths"),
        ("worktree", 7, "worktree"),
        ("owner", [], "owner"),
        ("status", None, "status"),
    ],
)
def test_malformed_covering_claim_fails_closed(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    claim = _claim(tmp_path)
    claim[field] = value
    with pytest.raises(ClaimCoverageError, match=message):
        decide_claim_coverage(
            {"active_claims": [claim]},
            identity="seat/one",
            root=tmp_path,
            branch="main",
            paths=("src/module.py",),
        )


def test_non_string_target_and_error_code_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ClaimCoverageError) as caught:
        decide_claim_coverage(
            {"active_claims": []},
            identity="seat/one",
            root=tmp_path,
            branch="main",
            paths=("src/module.py", 7),  # type: ignore[arg-type]  # runtime boundary
        )
    assert error_code(caught.value) == "claim_coverage"
    assert isinstance(caught.value, RuntimeError)


def test_unresolvable_claim_and_target_worktrees_fail_closed(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    claim = _claim(tmp_path)
    claim["worktree"] = str(loop)
    with pytest.raises(ClaimCoverageError, match="claim worktree"):
        decide_claim_coverage(
            {"active_claims": [claim]},
            identity="seat/one",
            root=tmp_path,
            branch="main",
            paths=("src/module.py",),
        )
    with pytest.raises(ClaimCoverageError, match="coverage worktree"):
        decide_claim_coverage(
            {"active_claims": []},
            identity="seat/one",
            root=loop,
            branch="main",
            paths=("src/module.py",),
        )
