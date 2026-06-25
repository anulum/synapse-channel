# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for git-hook auto-release of branch-scoped claims

from __future__ import annotations

from synapse_channel.git.githook import (
    changed_files,
)


def test_changed_files_commit_uses_diff_tree() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "src/a.py\nsrc/b.py\n"

    assert changed_files("commit", runner=runner) == ["src/a.py", "src/b.py"]
    assert captured == [["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"]]


def test_changed_files_merge_uses_orig_head() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "x\n"

    changed_files("merge", runner=runner)
    assert captured == [["diff", "--name-only", "ORIG_HEAD", "HEAD"]]


def test_changed_files_drops_blank_lines() -> None:
    assert changed_files("commit", runner=lambda _a: "a\n\n   \nb\n") == ["a", "b"]
