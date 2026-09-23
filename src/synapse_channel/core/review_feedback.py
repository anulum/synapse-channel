# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — immutable review provenance and independent disposition
"""Bind untrusted review evidence to exact work and a separate hub decision."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess  # nosec B404
from dataclasses import asdict, dataclass
from pathlib import Path

from synapse_channel.core.approvals import ApprovalReport

_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SEAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
_MAX_PATCH_BYTES = 4 * 1024 * 1024


class ReviewFeedbackError(ValueError):
    """A review source or author binding cannot support safe routing."""


def _identifier(value: object, field: str, *, pattern: re.Pattern[str] = _IDENTIFIER) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ReviewFeedbackError(f"{field} is invalid")
    return value


def _bounded(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise ReviewFeedbackError(f"{field} must be bounded printable text")
    return value


@dataclass(frozen=True)
class CommitPatch:
    """Exact commit diff and line-number-insensitive patch identity."""

    commit: str
    diff_sha256: str
    patch_id: str


@dataclass(frozen=True)
class AuthorBinding:
    """Author-declared task and native session attached to one exact commit."""

    repository: str
    commit: str
    diff_sha256: str
    patch_id: str
    task_id: str
    author_seat: str
    author_session: str

    def __post_init__(self) -> None:
        """Reject malformed bindings before persistence or delivery."""
        _identifier(self.repository, "repository", pattern=_REPOSITORY)
        _identifier(self.commit, "commit", pattern=_SHA)
        _identifier(self.diff_sha256, "diff_sha256", pattern=re.compile(r"[0-9a-f]{64}\Z"))
        _identifier(self.patch_id, "patch_id", pattern=_SHA)
        _identifier(self.task_id, "task_id")
        _identifier(self.author_seat, "author_seat", pattern=_SEAT)
        _bounded(self.author_session, "author_session", 256)


@dataclass(frozen=True)
class ReviewFinding:
    """Immutable source review with owner-classified evidence and verification."""

    repository: str
    review_id: int
    delivery_id: str
    pull_number: int
    reviewed_commit: str
    observed_head: str
    github_reviewer: str
    github_author: str | None
    github_state: str
    body: str
    severity: str
    evidence: str
    expected_verification: str
    source_kind: str
    source_path: str | None
    source_line: int | None
    source_sha256: str

    def __post_init__(self) -> None:
        """Bound every field without granting reviewer text decision authority."""
        _identifier(self.repository, "repository", pattern=_REPOSITORY)
        if type(self.review_id) is not int or self.review_id <= 0:
            raise ReviewFeedbackError("review_id must be positive")
        _identifier(self.delivery_id, "delivery_id")
        if type(self.pull_number) is not int or self.pull_number <= 0:
            raise ReviewFeedbackError("pull_number must be positive")
        _identifier(self.reviewed_commit, "reviewed_commit", pattern=_SHA)
        _identifier(self.observed_head, "observed_head", pattern=_SHA)
        _bounded(self.github_reviewer, "github_reviewer", 96)
        if self.github_author is not None:
            _bounded(self.github_author, "github_author", 96)
        _identifier(self.github_state, "github_state")
        if len(self.body.encode("utf-8")) > 32768:
            raise ReviewFeedbackError("review body exceeds its bound")
        if self.severity not in _SEVERITIES:
            raise ReviewFeedbackError("severity is invalid")
        _bounded(self.evidence, "evidence", 2048)
        _bounded(self.expected_verification, "expected_verification", 2048)
        if self.source_kind not in {"pull_request_review", "pull_request_review_comment"}:
            raise ReviewFeedbackError("review source kind is invalid")
        if self.source_kind == "pull_request_review_comment":
            if self.source_path is None:
                raise ReviewFeedbackError("review comment requires a source path")
            _bounded(self.source_path, "source_path", 1024)
            if (
                self.source_path.startswith("/")
                or "\\" in self.source_path
                or any(segment in {"", ".", ".."} for segment in self.source_path.split("/"))
            ):
                raise ReviewFeedbackError("source_path must be repository-relative")
        elif self.source_path is not None or self.source_line is not None:
            raise ReviewFeedbackError("summary review has no source line")
        if self.source_line is not None and (
            type(self.source_line) is not int or self.source_line < 1
        ):
            raise ReviewFeedbackError("source_line must be positive")
        _identifier(self.source_sha256, "source_sha256", pattern=re.compile(r"[0-9a-f]{64}\Z"))

    @property
    def key(self) -> str:
        """Return a stable repository-scoped review identifier."""
        return f"{self.repository}:{self.source_kind}:{self.review_id}"


def review_subject(finding: ReviewFinding, binding: AuthorBinding) -> str:
    """Bind source evidence and the exact author task/session to one decision."""
    if binding.repository != finding.repository or binding.commit != finding.reviewed_commit:
        raise ReviewFeedbackError("review and author binding do not refer to the same work")
    encoded = json.dumps(
        {"finding": asdict(finding), "binding": asdict(binding)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "review-feedback:" + hashlib.sha256(encoded).hexdigest()


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise ReviewFeedbackError("Git executable is unavailable")
    try:
        result = subprocess.run(  # nosec B603
            [executable, "-C", str(repo), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ReviewFeedbackError("Git commit evidence is unavailable") from exc
    if len(result.stdout) > _MAX_PATCH_BYTES:
        raise ReviewFeedbackError("Git commit evidence exceeds its bound")
    return result.stdout


def inspect_commit(repo: Path, commit: str) -> CommitPatch:
    """Read one local single-parent commit and both exact and stable diff IDs.

    Parameters
    ----------
    repo : pathlib.Path
        Local repository checkout containing the object.
    commit : str
        Full 40- or 64-character commit object id.

    Returns
    -------
    CommitPatch
        Exact diff SHA-256 and Git stable patch id.

    Raises
    ------
    ReviewFeedbackError
        If the object, parent count or patch is unsuitable.
    """
    _identifier(commit, "commit", pattern=_SHA)
    root = _git(repo, "rev-parse", "--show-toplevel").decode().strip()
    if Path(root).resolve() != repo.resolve():
        raise ReviewFeedbackError("repository path must be the Git root")
    line = _git(repo, "rev-list", "--parents", "-n", "1", commit).decode().strip().split()
    if len(line) != 2 or line[0] != commit:
        raise ReviewFeedbackError("reviewed commit must have exactly one parent")
    patch = _git(repo, "diff-tree", "--no-commit-id", "--binary", "-p", line[1], commit)
    if not patch:
        raise ReviewFeedbackError("reviewed commit has no diff")
    patch_line = _git(repo, "patch-id", "--stable", input_bytes=patch).decode().strip().split()
    if len(patch_line) < 1 or _SHA.fullmatch(patch_line[0]) is None:
        raise ReviewFeedbackError("Git could not identify the patch")
    return CommitPatch(
        commit=commit,
        diff_sha256=hashlib.sha256(patch).hexdigest(),
        patch_id=patch_line[0],
    )


def bind_author(
    repo: Path,
    *,
    repository: str,
    commit: str,
    task_id: str,
    author_seat: str,
    author_session: str,
) -> AuthorBinding:
    """Bind a commit's own seat trailer to its task and native session token."""
    patch = inspect_commit(repo, commit)
    _identifier(author_seat, "author_seat", pattern=_SEAT)
    trailers = _git(repo, "show", "-s", "--format=%B", commit).decode("utf-8")
    seat_lines = [
        line.removeprefix("Seat: ") for line in trailers.splitlines() if line.startswith("Seat: ")
    ]
    if seat_lines != [author_seat.rsplit("-", 1)[-1]]:
        raise ReviewFeedbackError("commit seat trailer does not match the author seat")
    return AuthorBinding(
        repository=repository,
        commit=commit,
        diff_sha256=patch.diff_sha256,
        patch_id=patch.patch_id,
        task_id=task_id,
        author_seat=author_seat,
        author_session=author_session,
    )


def applicability(binding: AuthorBinding, current: CommitPatch | None) -> str:
    """Classify a current commit without treating a rebase as automatic review."""
    if current is None:
        return "current_diff_unknown"
    if current.commit == binding.commit and current.diff_sha256 == binding.diff_sha256:
        return "exact_commit"
    if current.patch_id == binding.patch_id:
        return "same_patch_rebased_requires_recheck"
    return "stale_diff"


def independent_decision(
    finding: ReviewFinding,
    binding: AuthorBinding | None,
    approvals: ApprovalReport,
    *,
    reviewer_seat: str,
) -> str:
    """Read an existing approval decision without letting an author self-accept.

    An authenticated GitHub webhook proves source integrity, not that its
    reviewer login is a Synapse identity or that the body is an instruction.
    Only the latest hub approval on this exact immutable finding can decide it.
    """
    _identifier(reviewer_seat, "reviewer_seat", pattern=_SEAT)
    if binding is None:
        return "missing_author_binding"
    if reviewer_seat == binding.author_seat:
        return "not_independent"
    status = approvals.by_subject.get(review_subject(finding, binding))
    if status is None or status.decided_by != reviewer_seat:
        return "awaiting_independent_decision"
    if status.current_state == "approved":
        return "accepted"
    if status.current_state == "rejected":
        return "rejected"
    return "awaiting_independent_decision"
