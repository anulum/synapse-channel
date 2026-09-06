# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — neutral advisory Check Run projection
"""Render bounded, injection-safe GitHub Check Run request bodies."""

from __future__ import annotations

import html
from dataclasses import dataclass

from synapse_github_app.conflicts import ConflictNotice, ConflictReport
from synapse_github_app.errors import IncompleteAnalysisError

CHECK_NAME = "Synapse: predicted file-scope conflicts"
MAX_SUMMARY_CHARS = 60_000
MAX_PATHS_PER_NOTICE = 50


def _code(value: str) -> str:
    """Escape untrusted text inside an HTML code element."""
    return f"<code>{html.escape(value, quote=True)}</code>"


def _actor(login: str | None) -> str:
    """Render an escaped author label without adding a mention prefix."""
    if not login:
        return ""
    return f" by {_code(login)}"


def _notice_lines(notice: ConflictNotice) -> list[str]:
    """Render a bounded list of escaped overlap paths."""
    shown = notice.paths[:MAX_PATHS_PER_NOTICE]
    lines = [
        f"- PR #{notice.other_number}{_actor(notice.other_author)} on "
        f"{_code(notice.other_head_ref)} overlaps on {len(notice.paths)} path(s):"
    ]
    lines.extend(f"  - {_code(path)}" for path in shown)
    if len(notice.paths) > len(shown):
        lines.append(f"  - _{len(notice.paths) - len(shown)} additional path(s) omitted_ ")
    return lines


def _summary(report: ConflictReport) -> str:
    """Disclose incomplete inventories and bounded advisory evidence."""
    lines = [
        "This check is advisory. It does not reserve files, assign work, or block a merge.",
        "",
    ]
    if report.notices:
        lines.append(
            f"Observed file-scope overlap with {len(report.notices)} open pull request(s) "
            f"while evaluating {report.evaluated_pull_requests} pull request(s)."
        )
        lines.append(
            f"Pull request #{report.current_number}{_actor(report.current_author)} "
            "changes files in the overlaps listed below."
        )
        lines.append(
            "Attribution is by pull-request author; it does not rank, score, or gate any agent."
        )
        lines.append("A pull-request author is not evidence of a SYNAPSE claim or agent identity.")
        lines.append("")
        for notice in report.notices:
            lines.extend(_notice_lines(notice))
    else:
        lines.append(
            f"No file-scope overlap was observed across {report.evaluated_pull_requests} "
            "evaluated pull request(s)."
        )
    if not report.complete:
        lines.extend(["", "**Evidence is incomplete.**"])
        if report.open_pull_requests_truncated:
            lines.append("- The open pull-request inventory reached its 100-item bound.")
        if report.truncated_file_inventories:
            numbers = ", ".join(f"#{number}" for number in report.truncated_file_inventories)
            lines.append(
                f"- Changed-file inventories reached their 3,000-path bound for {numbers}."
            )
        lines.append("Absence of an additional conflict is not established.")
    rendered = "\n".join(lines)
    if len(rendered) <= MAX_SUMMARY_CHARS:
        return rendered
    suffix = "\n\n_Output truncated at the integration safety bound._"
    return rendered[: MAX_SUMMARY_CHARS - len(suffix)] + suffix


@dataclass(frozen=True)
class CheckRunRequest:
    """Completed neutral Check Run request accepted by GitHub REST."""

    head_sha: str
    external_id: str
    title: str
    summary: str

    def as_payload(self) -> dict[str, object]:
        """Return the exact JSON body for ``POST /check-runs``."""
        return {
            "name": CHECK_NAME,
            "head_sha": self.head_sha,
            "status": "completed",
            "conclusion": "neutral",
            "external_id": self.external_id,
            "output": {"title": self.title, "summary": self.summary},
        }


def build_check_run(report: ConflictReport, *, delivery_id: str) -> CheckRunRequest:
    """Build one advisory check, refusing a false clean from partial evidence."""
    if not report.complete and not report.notices:
        raise IncompleteAnalysisError("incomplete evidence cannot support a no-conflict check")
    if report.notices:
        title = f"Predicted overlap with {len(report.notices)} open pull request(s)"
        if not report.complete:
            title += " (partial evidence)"
    else:
        title = "No file-scope overlap observed"
    return CheckRunRequest(
        head_sha=report.head_sha,
        external_id=f"synapse:pr:{report.current_number}:delivery:{delivery_id}",
        title=title,
        summary=_summary(report),
    )
