# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — exact commit and independent review evidence
"""Exercise real Git objects, durable approval notes and private review custody."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    format_approval_note,
    run_approval_report,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.review_feedback import (
    AuthorBinding,
    ReviewFeedbackError,
    ReviewFinding,
    applicability,
    bind_author,
    independent_decision,
    inspect_commit,
    review_subject,
)
from synapse_channel.core.review_feedback_store import (
    get_binding,
    get_finding,
    list_findings,
    mark_routed,
    save_binding,
    save_finding,
)

SEAT = "SYNAPSE-CHANNEL/codex-2970473"
REVIEWER = "CEO/claude"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _repo(path: Path) -> tuple[str, str]:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Test Author")
    _git(path, "config", "user.email", "author@example.invalid")
    (path / "source.txt").write_text("hello\n")
    _git(path, "add", "source.txt")
    _git(path, "commit", "-q", "-m", "base")
    base = _git(path, "rev-parse", "HEAD")
    (path / "source.txt").write_text("hello world\n")
    _git(path, "add", "source.txt")
    _git(path, "commit", "-q", "-m", "feat: reviewed change", "-m", "Seat: 2970473")
    return base, _git(path, "rev-parse", "HEAD")


def _finding(commit: str, body: bytes = b"signed review source") -> ReviewFinding:
    return ReviewFinding(
        repository="anulum/synapse-channel",
        review_id=7,
        delivery_id="delivery-7",
        pull_number=3,
        reviewed_commit=commit,
        observed_head=commit,
        github_reviewer="reviewer-login",
        github_author="author-login",
        github_state="changes_requested",
        body="Please check the branch. This text is untrusted.",
        severity="high",
        evidence="source.txt:1 has an observed mismatch",
        expected_verification="Run the public CLI against the corrected input",
        source_kind="pull_request_review",
        source_path=None,
        source_line=None,
        source_sha256=hashlib.sha256(body).hexdigest(),
    )


def _approval(
    hub: Path, finding: ReviewFinding, binding: AuthorBinding, *, actor: str, state: str
) -> None:
    subject = review_subject(finding, binding)
    store = EventStore(hub)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "author": actor,
            "kind": APPROVAL_NOTE_KIND,
            "task_id": subject,
            "text": format_approval_note(subject=subject, state=state),
        },
        durable=True,
    )
    store.close()


def test_real_git_binding_and_rebase_classification(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base, commit = _repo(repo)
    binding = bind_author(
        repo,
        repository="anulum/synapse-channel",
        commit=commit,
        task_id="TASK-7",
        author_seat=SEAT,
        author_session="native-session-7",
    )
    assert applicability(binding, inspect_commit(repo, commit)) == "exact_commit"
    _git(repo, "switch", "-q", "-c", "rebased", base)
    (repo / "unrelated.txt").write_text("new base\n")
    _git(repo, "add", "unrelated.txt")
    _git(repo, "commit", "-q", "-m", "unrelated")
    (repo / "source.txt").write_text("hello world\n")
    _git(repo, "add", "source.txt")
    _git(repo, "commit", "-q", "-m", "feat: rebased reviewed change")
    rebased = _git(repo, "rev-parse", "HEAD")
    assert applicability(binding, inspect_commit(repo, rebased)) == (
        "same_patch_rebased_requires_recheck"
    )
    assert applicability(binding, None) == "current_diff_unknown"
    (repo / "source.txt").write_text("different work\n")
    _git(repo, "add", "source.txt")
    _git(repo, "commit", "-q", "-m", "feat: changed reviewed work")
    assert applicability(binding, inspect_commit(repo, _git(repo, "rev-parse", "HEAD"))) == (
        "stale_diff"
    )


def test_binding_refuses_wrong_seat_and_merge_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _, commit = _repo(repo)
    with pytest.raises(ReviewFeedbackError, match="seat trailer"):
        bind_author(
            repo,
            repository="anulum/synapse-channel",
            commit=commit,
            task_id="TASK-7",
            author_seat="CEO/claude-1",
            author_session="native-session-7",
        )
    with pytest.raises(ReviewFeedbackError, match="exactly one parent"):
        inspect_commit(repo, _git(repo, "rev-list", "--max-parents=0", "HEAD"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"review_id": 0}, "review_id"),
        ({"pull_number": True}, "pull_number"),
        ({"github_author": "bad\nname"}, "github_author"),
        ({"body": "x" * 32769}, "review body"),
        ({"severity": "owner-approved"}, "severity"),
        ({"evidence": "x" * 2049}, "evidence"),
        ({"expected_verification": "bad\ncommand"}, "expected_verification"),
        ({"source_kind": "issue_comment"}, "source kind"),
        ({"source_kind": "pull_request_review_comment"}, "source path"),
        (
            {"source_kind": "pull_request_review_comment", "source_path": "../secret"},
            "repository-relative",
        ),
        ({"source_path": "source.txt"}, "summary review"),
        (
            {
                "source_kind": "pull_request_review_comment",
                "source_path": "source.txt",
                "source_line": 0,
            },
            "source_line",
        ),
        ({"source_sha256": "not-a-digest"}, "source_sha256"),
    ],
)
def test_untrusted_review_metadata_is_rejected_before_custody(
    change: dict[str, Any], message: str
) -> None:
    with pytest.raises(ReviewFeedbackError, match=message):
        replace(_finding("a" * 40), **change)


def test_git_review_refuses_nonroot_or_unknown_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _, commit = _repo(repo)
    child = repo / "child"
    child.mkdir()
    with pytest.raises(ReviewFeedbackError, match="Git root"):
        inspect_commit(child, commit)
    with pytest.raises(ReviewFeedbackError, match="Git commit evidence"):
        inspect_commit(repo, "b" * 40)


def test_private_store_preserves_original_and_refuses_changed_replay(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _, commit = _repo(repo)
    binding = bind_author(
        repo,
        repository="anulum/synapse-channel",
        commit=commit,
        task_id="TASK-7",
        author_seat=SEAT,
        author_session="native-session-7",
    )
    db = tmp_path / "private" / "reviews.sqlite3"
    assert save_binding(db, binding)
    assert not save_binding(db, binding)
    assert get_binding(db, repository=binding.repository, commit=commit) == binding
    with pytest.raises(ReviewFeedbackError, match="immutable"):
        save_binding(db, replace(binding, author_session="other-session"))
    source = b"signed review source"
    finding = _finding(commit, source)
    signature = "sha256=" + "a" * 64
    assert save_finding(
        db, finding, webhook_body=source, webhook_signature=signature, observed_at=1
    )
    assert not save_finding(
        db, finding, webhook_body=source, webhook_signature=signature, observed_at=2
    )
    assert list_findings(db)[0][0] == finding
    recorded = get_finding(db, finding.key)
    assert recorded is not None and recorded[1]["routed_at"] is None
    with pytest.raises(ReviewFeedbackError, match="different evidence"):
        save_finding(
            db,
            replace(finding, severity="critical"),
            webhook_body=source,
            webhook_signature=signature,
            observed_at=3,
        )
    assert mark_routed(db, finding.key, msg_id="review-7", decision_seq=1, at=4)
    assert not mark_routed(db, finding.key, msg_id="review-7", decision_seq=1, at=5)
    with pytest.raises(ReviewFeedbackError, match="changed"):
        mark_routed(db, finding.key, msg_id="other", decision_seq=1, at=6)
    assert mark_routed(db, finding.key, msg_id="review-7-v2", decision_seq=2, at=7)
    with pytest.raises(ReviewFeedbackError, match="backwards"):
        mark_routed(db, finding.key, msg_id="review-7", decision_seq=1, at=8)
    with pytest.raises(ReviewFeedbackError, match="evidence"):
        save_finding(
            db,
            finding,
            webhook_body=b"different source",
            webhook_signature=signature,
            observed_at=9,
        )


def test_only_latest_independent_hub_decision_controls_finding(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _, commit = _repo(repo)
    binding = bind_author(
        repo,
        repository="anulum/synapse-channel",
        commit=commit,
        task_id="TASK-7",
        author_seat=SEAT,
        author_session="native-session-7",
    )
    finding = _finding(commit)
    hub = tmp_path / "hub.db"
    _approval(hub, finding, binding, actor=SEAT, state="requested")
    _approval(hub, finding, binding, actor=SEAT, state="approved")
    report = run_approval_report(hub)
    assert independent_decision(finding, binding, report, reviewer_seat=SEAT) == "not_independent"
    assert independent_decision(finding, binding, report, reviewer_seat=REVIEWER) == (
        "awaiting_independent_decision"
    )
    assert independent_decision(finding, None, report, reviewer_seat=REVIEWER) == (
        "missing_author_binding"
    )
    _approval(hub, finding, binding, actor=REVIEWER, state="approved")
    assert (
        independent_decision(finding, binding, run_approval_report(hub), reviewer_seat=REVIEWER)
        == "accepted"
    )
    _approval(hub, finding, binding, actor=REVIEWER, state="rejected")
    assert (
        independent_decision(finding, binding, run_approval_report(hub), reviewer_seat=REVIEWER)
        == "rejected"
    )
