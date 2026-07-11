# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — pull-request conflict projection
"""Map bounded pull-request snapshots onto SYNAPSE's core conflict finder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from synapse_channel.git.gitconflict import find_conflicts

from synapse_github_app.errors import PayloadError
from synapse_github_app.models import PullRequestSnapshot


@dataclass(frozen=True)
class ConflictNotice:
    """One open pull request whose changed paths overlap the event PR."""

    other_number: int
    other_head_ref: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class ConflictReport:
    """Evidence-bounded advisory conflict analysis for one pull request."""

    current_number: int
    head_sha: str
    notices: tuple[ConflictNotice, ...]
    complete: bool
    evaluated_pull_requests: int
    open_pull_requests_truncated: bool
    truncated_file_inventories: tuple[int, ...]


def analyse_conflicts(
    current: PullRequestSnapshot,
    snapshots: tuple[PullRequestSnapshot, ...],
    *,
    open_pull_requests_truncated: bool,
) -> ConflictReport:
    """Find overlaps involving ``current`` through the canonical core function."""
    by_number: dict[int, PullRequestSnapshot] = {}
    for snapshot in snapshots:
        if snapshot.number in by_number:
            raise PayloadError("pull-request snapshots contain a duplicate number")
        by_number[snapshot.number] = snapshot
    recorded_current = by_number.get(current.number)
    if recorded_current != current:
        raise PayloadError("the event pull request is missing from the evaluated snapshots")

    claims: list[dict[str, Any]] = []
    by_branch: dict[str, PullRequestSnapshot] = {}
    for snapshot in snapshots:
        if not snapshot.paths:
            continue
        by_branch[snapshot.branch_key] = snapshot
        claims.append(
            {
                "owner": f"PR #{snapshot.number}",
                "paths": list(snapshot.paths),
                "git": {"branch": snapshot.branch_key, "base": snapshot.base_ref},
            }
        )

    notices: list[ConflictNotice] = []
    for conflict in find_conflicts(claims):
        if conflict.branch_a == current.branch_key:
            other = by_branch[conflict.branch_b]
        elif conflict.branch_b == current.branch_key:
            other = by_branch[conflict.branch_a]
        else:
            continue
        notices.append(
            ConflictNotice(
                other_number=other.number,
                other_head_ref=other.head_ref,
                paths=conflict.paths,
            )
        )
    notices.sort(key=lambda item: (item.other_number, item.paths))
    truncated_files = tuple(
        sorted(snapshot.number for snapshot in snapshots if snapshot.paths_truncated)
    )
    return ConflictReport(
        current_number=current.number,
        head_sha=current.head_sha,
        notices=tuple(notices),
        complete=not open_pull_requests_truncated and not truncated_files,
        evaluated_pull_requests=len(snapshots),
        open_pull_requests_truncated=open_pull_requests_truncated,
        truncated_file_inventories=truncated_files,
    )
