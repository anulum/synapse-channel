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
