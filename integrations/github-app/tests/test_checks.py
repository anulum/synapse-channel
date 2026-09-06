# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — neutral bounded Check Run tests
"""Verify advisory conclusions, honest incompleteness, and rendering safety."""

from __future__ import annotations

import pytest

from synapse_github_app.checks import MAX_PATHS_PER_NOTICE, MAX_SUMMARY_CHARS, build_check_run
from synapse_github_app.conflicts import ConflictNotice, ConflictReport
from synapse_github_app.errors import IncompleteAnalysisError


def _report(
    *,
    notices: tuple[ConflictNotice, ...] = (),
    complete: bool = True,
    pulls_truncated: bool = False,
    files_truncated: tuple[int, ...] = (),
    current_author: str | None = None,
) -> ConflictReport:
    """Build an advisory report with explicit completeness and attribution."""
    return ConflictReport(
        current_number=7,
        head_sha="7" * 40,
        notices=notices,
        complete=complete,
        evaluated_pull_requests=2,
        open_pull_requests_truncated=pulls_truncated,
        truncated_file_inventories=files_truncated,
        current_author=current_author,
    )


def test_clean_complete_check_remains_neutral_and_advisory() -> None:
    """Keep no-overlap conclusions neutral and non-reserving."""
    check = build_check_run(_report(), delivery_id="delivery-7")
    payload = check.as_payload()

    assert payload["status"] == "completed"
    assert payload["conclusion"] == "neutral"
    assert payload["head_sha"] == "7" * 40
    assert payload["external_id"] == "synapse:pr:7:delivery:delivery-7"
    output = payload["output"]
    assert isinstance(output, dict)
    assert output["title"] == "No file-scope overlap observed"
    assert "does not reserve files" in output["summary"]


def test_overlap_check_escapes_untrusted_ref_and_path() -> None:
    """Escape branch and path markup in check output."""
    notice = ConflictNotice(
        other_number=9,
        other_head_ref='feature/<img src=x onerror="alert(1)">',
        paths=("src/<script>.py",),
    )
    check = build_check_run(_report(notices=(notice,)), delivery_id="delivery")

    assert check.title == "Predicted overlap with 1 open pull request(s)"
    assert "<img" not in check.summary
    assert "&lt;img" in check.summary
    assert "&lt;script&gt;" in check.summary


def test_attribution_names_authors_without_mentioning_them() -> None:
    """Display author labels without claiming reservation ownership."""
    notice = ConflictNotice(
        other_number=9,
        other_head_ref="feature/9",
        paths=("src/a.py",),
        other_author="dependabot[bot]",
    )
    check = build_check_run(
        _report(notices=(notice,), current_author="octo-dev"),
        delivery_id="delivery",
    )

    assert "Pull request #7 by <code>octo-dev</code>" in check.summary
    assert "does not rank, score, or gate any agent" in check.summary
    assert "PR #9 by <code>dependabot[bot]</code> on" in check.summary
    assert "@" not in check.summary
    assert "not evidence of a SYNAPSE claim or agent identity" in check.summary
    assert "holds the file-scope claims" not in check.summary


def test_attribution_is_omitted_for_unattributed_pull_requests() -> None:
    """Omit labels for unattributed pull requests."""
    notice = ConflictNotice(other_number=9, other_head_ref="feature/9", paths=("src/a.py",))
    check = build_check_run(_report(notices=(notice,)), delivery_id="delivery")

    assert "Pull request #7 changes files in the overlaps listed below." in check.summary
    assert "PR #9 on <code>feature/9</code>" in check.summary
    assert " by <code>@" not in check.summary


def test_author_login_is_html_escaped_in_attribution() -> None:
    """Escape author markup before displaying it."""
    notice = ConflictNotice(
        other_number=9,
        other_head_ref="feature/9",
        paths=("src/a.py",),
        other_author="<b>x",
    )
    check = build_check_run(
        _report(notices=(notice,), current_author="a&b"),
        delivery_id="delivery",
    )

    assert "&lt;b&gt;x" in check.summary
    assert "a&amp;b" in check.summary
    assert "<b>x" not in check.summary


def test_partial_overlap_is_honest_but_partial_clean_is_refused() -> None:
    """Refuse clean conclusions from incomplete evidence."""
    notice = ConflictNotice(other_number=9, other_head_ref="feature/9", paths=("src/a.py",))
    partial = build_check_run(
        _report(
            notices=(notice,),
            complete=False,
            pulls_truncated=True,
            files_truncated=(7, 9),
        ),
        delivery_id="delivery",
    )

    assert "partial evidence" in partial.title
    assert "Evidence is incomplete" in partial.summary
    assert "100-item bound" in partial.summary
    assert "#7, #9" in partial.summary
    with pytest.raises(IncompleteAnalysisError):
        build_check_run(_report(complete=False, pulls_truncated=True), delivery_id="delivery")


def test_each_incompleteness_note_is_independently_optional() -> None:
    """Disclose only the inventory limits that were reached."""
    notice = ConflictNotice(other_number=9, other_head_ref="feature/9", paths=("src/a.py",))
    pulls_only = build_check_run(
        _report(notices=(notice,), complete=False, pulls_truncated=True),
        delivery_id="pulls",
    )
    files_only = build_check_run(
        _report(notices=(notice,), complete=False, files_truncated=(9,)),
        delivery_id="files",
    )

    assert "100-item bound" in pulls_only.summary
    assert "3,000-path bound" not in pulls_only.summary
    assert "100-item bound" not in files_only.summary
    assert "3,000-path bound" in files_only.summary


def test_check_output_bounds_paths_and_total_summary() -> None:
    """Bound path listings and total summary size."""
    paths = tuple(f"src/{index:04d}-{'x' * 1000}.py" for index in range(MAX_PATHS_PER_NOTICE + 10))
    notices = tuple(
        ConflictNotice(other_number=index + 10, other_head_ref=f"feature/{index}", paths=paths)
        for index in range(3)
    )
    check = build_check_run(_report(notices=notices), delivery_id="delivery")

    assert "10 additional path(s) omitted" in check.summary
    assert len(check.summary) <= MAX_SUMMARY_CHARS
    assert check.summary.endswith("_Output truncated at the integration safety bound._")
