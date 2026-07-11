# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — canonical conflict projection tests
"""Prove PR inventories reuse the shipped core overlap semantics."""

from __future__ import annotations

import pytest

from synapse_github_app.conflicts import analyse_conflicts
from synapse_github_app.errors import PayloadError
from synapse_github_app.models import PullRequestSnapshot


def _snapshot(
    number: int,
    paths: tuple[str, ...],
    *,
    base: str = "main",
    truncated: bool = False,
) -> PullRequestSnapshot:
    return PullRequestSnapshot(
        number=number,
        head_sha=f"{number:040x}",
        head_ref=f"feature/{number}",
        base_ref=base,
        paths=paths,
        paths_truncated=truncated,
    )


def test_directory_overlap_flows_through_core_finder() -> None:
    current = _snapshot(7, ("src/auth", "README.md"))
    other = _snapshot(9, ("src/auth/tokens.py", "docs/guide.md"))

    report = analyse_conflicts(current, (current, other), open_pull_requests_truncated=False)

    assert report.complete is True
    assert report.evaluated_pull_requests == 2
    assert len(report.notices) == 1
    assert report.notices[0].other_number == 9
    assert report.notices[0].paths == ("src/auth/tokens.py",)


def test_current_may_be_second_and_unrelated_other_pairs_are_ignored() -> None:
    current = _snapshot(7, ("src/shared.py",))
    other = _snapshot(9, ("src/shared.py", "docs/same.md"))
    third = _snapshot(10, ("docs/same.md",))

    report = analyse_conflicts(
        current,
        (other, third, current),
        open_pull_requests_truncated=False,
    )

    assert [(notice.other_number, notice.paths) for notice in report.notices] == [
        (9, ("src/shared.py",))
    ]


def test_same_base_is_required_and_empty_pr_is_not_whole_worktree() -> None:
    current = _snapshot(1, ("src/a.py",))
    other_base = _snapshot(2, ("src/a.py",), base="release")
    empty = _snapshot(3, ())

    report = analyse_conflicts(
        current,
        (current, other_base, empty),
        open_pull_requests_truncated=False,
    )
    assert report.notices == ()


def test_report_records_both_inventory_incompleteness_sources() -> None:
    current = _snapshot(1, ("src/a.py",), truncated=True)
    other = _snapshot(2, ("src/a.py",), truncated=True)

    report = analyse_conflicts(current, (current, other), open_pull_requests_truncated=True)

    assert report.complete is False
    assert report.open_pull_requests_truncated is True
    assert report.truncated_file_inventories == (1, 2)
    assert report.notices[0].other_number == 2


def test_duplicate_or_missing_current_is_refused() -> None:
    current = _snapshot(1, ("a",))
    with pytest.raises(PayloadError, match="duplicate"):
        analyse_conflicts(current, (current, current), open_pull_requests_truncated=False)
    with pytest.raises(PayloadError, match="missing"):
        analyse_conflicts(current, (_snapshot(2, ("a",)),), open_pull_requests_truncated=False)
