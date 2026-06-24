# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for file-scope overlap detection

from __future__ import annotations

import pytest

from synapse_channel.core.scoping import (
    MAX_DECLARED_PATHS,
    normalize_path,
    normalize_paths,
    paths_overlap,
    scopes_conflict,
)

# --- normalize_path ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  src/  ", "src"),
        ("./src/foo.py", "src/foo.py"),
        ("././pkg", "pkg"),
        ("/", ""),
        ("", ""),
        ("a/b/c", "a/b/c"),
        ("src//app.py", "src/app.py"),  # duplicate slashes collapse
        ("src/../tests", "tests"),  # .. resolves against the preceding segment
        ("a/b/../c", "a/c"),
        ("a/b/../../c", "c"),  # .. up twice lands at the root then descends
        ("../../etc/passwd", "../../etc/passwd"),  # leading .. (out of tree) kept literal
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


# --- paths_overlap -----------------------------------------------------------


def test_identical_paths_overlap() -> None:
    assert paths_overlap("src/app.py", "src/app.py") is True


def test_ancestor_overlaps_descendant_both_directions() -> None:
    assert paths_overlap("src", "src/app.py") is True
    assert paths_overlap("src/app.py", "src") is True


def test_root_overlaps_everything() -> None:
    assert paths_overlap("", "anything/here.py") is True
    assert paths_overlap("deep/file", "") is True


def test_sibling_paths_do_not_overlap() -> None:
    assert paths_overlap("src", "tests") is False
    assert paths_overlap("src/app.py", "src/util.py") is False


def test_dotdot_normalisation_catches_a_previously_missed_overlap() -> None:
    # Before segment normalisation these compared as distinct strings.
    assert paths_overlap("src/../tests/app.py", "tests") is True
    assert paths_overlap("tests//app.py", "tests/app.py") is True


def test_out_of_tree_path_does_not_overlap_an_in_tree_claim() -> None:
    # A leading .. is kept, so an out-of-tree path never collides with an in-tree name.
    assert paths_overlap("../etc/passwd", "etc/passwd") is False


def test_shared_prefix_but_not_directory_boundary_does_not_overlap() -> None:
    # "src" must not be treated as an ancestor of "srcfoo".
    assert paths_overlap("src", "srcfoo/x.py") is False


# --- scopes_conflict ---------------------------------------------------------


def test_different_worktrees_never_conflict() -> None:
    assert scopes_conflict("wt-a", ["src"], "wt-b", ["src"]) is False


def test_same_worktree_overlapping_paths_conflict() -> None:
    assert scopes_conflict("", ["src/app.py"], "", ["src"]) is True


def test_same_worktree_disjoint_paths_do_not_conflict() -> None:
    assert scopes_conflict("", ["src"], "", ["tests", "docs"]) is False


def test_empty_path_set_claims_whole_worktree() -> None:
    assert scopes_conflict("", [], "", ["src"]) is True
    assert scopes_conflict("", ["tests"], "", []) is True
    assert scopes_conflict("", [], "", []) is True


def test_empty_path_set_still_scoped_to_its_worktree() -> None:
    # Whole-worktree claim does not reach into a different worktree.
    assert scopes_conflict("", [], "other", ["src"]) is False


# --- normalize_paths ---------------------------------------------------------


def test_normalize_paths_dedups_and_preserves_order() -> None:
    assert normalize_paths(["src/", "./tests", "src", "docs"]) == ("src", "tests", "docs")


def test_normalize_paths_root_collapses_to_single_root() -> None:
    assert normalize_paths(["src", "/", "tests"]) == ("",)


def test_normalize_paths_empty_input() -> None:
    assert normalize_paths([]) == ()


def test_normalize_paths_widens_to_root_past_the_cap() -> None:
    # More distinct paths than the bound collapse to the whole worktree (conservative).
    many = [f"dir{i}/file" for i in range(MAX_DECLARED_PATHS + 5)]
    assert normalize_paths(many) == ("",)


def test_normalize_paths_keeps_a_set_at_the_cap() -> None:
    exactly = [f"dir{i}/file" for i in range(MAX_DECLARED_PATHS)]
    result = normalize_paths(exactly)
    assert len(result) == MAX_DECLARED_PATHS
    assert result != ("",)
