# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for git-hook auto-release of branch-scoped claims

from __future__ import annotations

from synapse_channel.git.githook import (
    _paths_overlap,
)
from synapse_channel.git.semantic_scope import semantic_scope_path


def test_paths_overlap_whole_worktree() -> None:
    assert _paths_overlap([], ["any.py"]) is True
    assert _paths_overlap([], []) is False


def test_paths_overlap_exact_prefix_and_miss() -> None:
    assert _paths_overlap(["src/a.py"], ["src/a.py"]) is True
    assert _paths_overlap(["src"], ["src/a.py"]) is True
    assert _paths_overlap(["src/"], ["src/a.py"]) is True
    assert _paths_overlap(["src/a.py"], ["src/b.py"]) is False
    assert _paths_overlap(["docs"], ["src/a.py"]) is False


def test_semantic_paths_release_only_exact_or_descendant_symbols() -> None:
    class_scope = semantic_scope_path("src/a.py", "Worker")
    method_scope = semantic_scope_path("src/a.py", "Worker.run")
    sibling_scope = semantic_scope_path("src/a.py", "Worker.stop")

    assert _paths_overlap([class_scope], [method_scope])
    assert _paths_overlap([method_scope], [method_scope])
    assert not _paths_overlap([method_scope], [class_scope])
    assert not _paths_overlap([method_scope], [sibling_scope])
    assert not _paths_overlap([method_scope], ["src/a.py"])
