# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared semantic enforcement projection regressions
"""Prove contextual semantic claims and conservative diff projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.git.semantic_diff import SemanticDiffRecord
from synapse_channel.git.semantic_enforcement import (
    SemanticEnforcementError,
    claim_paths_for_context,
    project_change_paths,
    semantic_claim_covers_source,
    semantic_sources_for_context,
    semantic_sources_from_paths,
)
from synapse_channel.git.semantic_scope import semantic_scope_path


def _claim(
    root: Path,
    *,
    paths: list[str],
    branch: str = "main",
) -> dict[str, Any]:
    return {
        "task_id": "TASK",
        "owner": "seat/one",
        "status": "claimed",
        "worktree": str(root),
        "paths": paths,
        "git": {"branch": branch, "base": "main", "auto_release_on": "manual"},
    }


def _record(
    source: str,
    *,
    claim_paths: tuple[str, ...],
    narrowed: bool,
) -> SemanticDiffRecord:
    return SemanticDiffRecord(
        status="M",
        source=source,
        old_source=source,
        language="python",
        symbols=("run",) if narrowed else (),
        semantic_scopes=claim_paths if narrowed else (),
        claim_paths=claim_paths,
        narrowed=narrowed,
        reason="test evidence",
    )


def test_claim_paths_require_the_exact_worktree_and_branch(tmp_path: Path) -> None:
    scope = semantic_scope_path("src/a.py", "run")
    claim = _claim(tmp_path, paths=[scope])

    assert claim_paths_for_context(claim, root=tmp_path.resolve(), branch="main") == (scope,)
    assert claim_paths_for_context(claim, root=tmp_path / "other", branch="main") is None
    assert claim_paths_for_context(claim, root=tmp_path.resolve(), branch="other") is None
    claim["worktree"] = ""
    assert claim_paths_for_context(claim, root=tmp_path.resolve(), branch="main") is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("worktree", 7, "worktree"),
        ("paths", "src/a.py", "paths"),
    ],
)
def test_malformed_contextual_claim_fails_closed(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    claim = _claim(tmp_path, paths=["src/a.py"])
    claim[field] = value
    with pytest.raises(SemanticEnforcementError, match=message):
        claim_paths_for_context(claim, root=tmp_path.resolve(), branch="main")


def test_semantic_source_decoding_is_canonical_stable_and_literal_safe() -> None:
    run = semantic_scope_path("src/a.py", "run")
    other = semantic_scope_path("src/a.py", "other")
    paths = (run, "src/a.py", "not/.synapse-symbol/%ZZ", other, run)

    assert semantic_sources_from_paths(paths) == ("src/a.py",)
    assert semantic_claim_covers_source(paths, "./src//a.py")
    assert not semantic_claim_covers_source(paths, "src/b.py")


def test_snapshot_sources_are_context_bound_filtered_and_deduplicated(
    tmp_path: Path,
) -> None:
    a_run = semantic_scope_path("src/a.py", "run")
    a_other = semantic_scope_path("src/a.py", "other")
    b_run = semantic_scope_path("src/b.py", "run")
    snapshot = {
        "active_claims": [
            _claim(tmp_path, paths=[a_run, a_other]),
            _claim(tmp_path, paths=[b_run]),
            _claim(tmp_path, paths=[semantic_scope_path("src/c.py", "run")], branch="other"),
        ]
    }

    assert semantic_sources_for_context(
        snapshot,
        root=tmp_path.resolve(),
        branch="main",
    ) == ("src/a.py", "src/b.py")
    assert semantic_sources_for_context(
        snapshot,
        root=tmp_path.resolve(),
        branch="main",
        targets=("src/b.py",),
    ) == ("src/b.py",)


@pytest.mark.parametrize(
    "snapshot",
    [
        {"active_claims": "bad"},
        {"active_claims": ["bad"]},
    ],
)
def test_malformed_snapshot_sources_fail_closed(
    tmp_path: Path,
    snapshot: dict[str, Any],
) -> None:
    with pytest.raises(SemanticEnforcementError):
        semantic_sources_for_context(snapshot, root=tmp_path.resolve(), branch="main")


def test_projection_replaces_only_proven_narrowed_sources() -> None:
    run = semantic_scope_path("src/a.py", "run")
    records = (
        _record("src/a.py", claim_paths=(run,), narrowed=True),
        _record("src/b.py", claim_paths=("src/b.py",), narrowed=False),
    )

    assert project_change_paths(("src/a.py", "src/b.py", "README.md"), records) == (
        run,
        "src/b.py",
        "README.md",
    )


def test_duplicate_narrowed_evidence_fails_closed() -> None:
    run = semantic_scope_path("src/a.py", "run")
    records = (
        _record("src/a.py", claim_paths=(run,), narrowed=True),
        _record("src/a.py", claim_paths=(run,), narrowed=True),
    )
    with pytest.raises(SemanticEnforcementError, match="duplicate source"):
        project_change_paths(("src/a.py",), records)


@pytest.mark.parametrize(
    "claim_paths",
    [
        (),
        ("src/a.py",),
        (semantic_scope_path("src/b.py", "run"),),
    ],
)
def test_invalid_narrowed_evidence_cannot_remove_a_physical_path(
    claim_paths: tuple[str, ...],
) -> None:
    record = _record("src/a.py", claim_paths=claim_paths, narrowed=True)
    with pytest.raises(SemanticEnforcementError, match="invalid narrowed"):
        project_change_paths(("src/a.py",), (record,))
