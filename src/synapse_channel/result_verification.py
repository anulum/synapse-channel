# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verify the requested result, not only the exchange
"""Keep three facts apart: communication finished, effect observed, result verified.

An execution outcome (:mod:`synapse_channel.execution_outcome`) says that a
boundary finished, for example that a worker process exited with status 0.
That does not say the requested work happened, or happened correctly. A
:class:`ResultVerification` records the three facts separately:

``communication``
    The state of the execution boundary, taken from an outcome report, or
    ``not_supplied``.
``effect``
    ``observed`` when the verifier saw the effect in the world, ``absent``
    when it looked and the effect is not there, ``unknown`` when it could not
    look.
``result``
    ``verified`` only when the observed effect is exactly the requested one,
    ``not_verified`` when it is not, and ``not_checked`` when the verifier
    could not decide.

Two verifiers are provided here:

* :func:`verify_file_change` checks a file's exact content by SHA-256 and can
  also run the change's own tests.
* :func:`verify_git_publication` checks that a commit is on a remote branch.
  Given an :class:`~synapse_channel.result_verification_attempt.AttemptCheck`,
  it also reads that attempt's marker in the same ``git ls-remote`` and
  answers by the attempt's reading (applied, sealed, absent or foreign).

Deployment and database-migration checks live in
:mod:`synapse_channel.result_verification_effects`. They only read as well.

A successful exit or a "done" reply never makes a result verified on its own.
Verifiers only read; they never repeat or repair the work.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404 # explicit argv only, never a shell
from collections.abc import Sequence
from pathlib import Path

from synapse_channel.core.attempt_markers import OBJECT_ID_LENGTHS, observation
from synapse_channel.core.result_verification_record import EFFECTS as EFFECTS
from synapse_channel.core.result_verification_record import RESULTS as RESULTS
from synapse_channel.core.result_verification_record import SCHEMA as SCHEMA
from synapse_channel.core.result_verification_record import (
    ResultVerification as ResultVerification,
)
from synapse_channel.core.result_verification_record import (
    verification_from_json as verification_from_json,
)
from synapse_channel.core.secure_path import open_nofollow_leaf
from synapse_channel.execution_outcome import OUTCOME_SCHEMA, OutcomeState
from synapse_channel.result_verification_attempt import (
    ATTEMPT_ROWS,
    AttemptCheck,
    identifier_length,
    listed_refs,
)

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_READ_CHUNK = 1 << 20
_NOFOLLOW: int | None = getattr(os, "O_NOFOLLOW", None)


def read_outcome(path: Path) -> tuple[str, str]:
    """Return ``(operation_id, state)`` of a ``synapse.execution-outcome.v1`` report.

    Parameters
    ----------
    path : pathlib.Path
        A JSON outcome report, for example from ``synapse worker-session``.

    Returns
    -------
    tuple[str, str]
        The report's operation identity and boundary state.

    Raises
    ------
    ValueError
        If the file is not such a report.
    OSError
        If it cannot be read.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("the outcome report is not JSON") from exc
    states = {state.value for state in OutcomeState}
    if (
        not isinstance(document, dict)
        or document.get("schema") != OUTCOME_SCHEMA
        or not isinstance(document.get("operation_id"), str)
        or not document["operation_id"]
        or document.get("state") not in states
    ):
        raise ValueError(f"the outcome report is not a {OUTCOME_SCHEMA} record")
    return document["operation_id"], document["state"]


def _digest(path: Path) -> str:
    """Return the SHA-256 of a regular file, read without following a final link."""
    if os.name == "nt":
        # Windows has no O_NOFOLLOW. The shared opener refuses a reparse point
        # and opens the leaf in binary mode.
        descriptor = open_nofollow_leaf(path)
    elif _NOFOLLOW is None:
        raise PermissionError("this platform cannot open a file without following a final link")
    else:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise IsADirectoryError(str(path))
    with os.fdopen(descriptor, "rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_tests(tests: Sequence[str], cwd: Path, timeout: float) -> tuple[str, str]:
    """Run the change's tests; return ``(result, observation)``."""
    try:
        completed = subprocess.run(  # noqa: S603  # nosec B603 # explicit argv, no shell
            list(tests), cwd=cwd, timeout=timeout, capture_output=True, check=False
        )
    except subprocess.TimeoutExpired:
        return "not_checked", "tests_timed_out"
    except OSError:
        return "not_checked", "tests_could_not_start"
    if completed.returncode == 0:
        return "verified", "content_matches_and_tests_passed"
    return "not_verified", f"tests_failed_with_status_{completed.returncode}"


def verify_file_change(
    path: Path,
    sha256: str,
    *,
    operation_id: str,
    communication: str = "not_supplied",
    tests: Sequence[str] = (),
    cwd: Path | None = None,
    timeout: float = 300.0,
) -> ResultVerification:
    """Verify that ``path`` holds exactly the requested content.

    Parameters
    ----------
    path : pathlib.Path
        The file the work was to produce or change.
    sha256 : str
        Lower-case hex SHA-256 of the requested content.
    operation_id : str
        The operation being verified.
    communication : str, optional
        The boundary state from the outcome report, or ``not_supplied``.
    tests : sequence of str, optional
        The change's own tests as an argv, run only when the content matches.
    cwd : pathlib.Path or None, optional
        Directory for the tests; defaults to the file's directory.
    timeout : float, optional
        Seconds the tests may run.

    Returns
    -------
    ResultVerification
        ``verified`` only for exact content and, when given, passing tests.

    Raises
    ------
    ValueError
        If ``sha256`` is not 64 lower-case hex digits or ``timeout`` is not positive.
    """
    if not _HEX64.fullmatch(sha256):
        raise ValueError("the requested content digest is 64 lower-case hex digits")
    if not 0.0 < timeout < float("inf"):
        raise ValueError("the test timeout is a positive, finite number of seconds")
    evidence = {"path": str(path), "requested_sha256": sha256}

    def record(effect: str, result: str, observation: str) -> ResultVerification:
        return ResultVerification(
            operation_id, "file_change", communication, effect, result, observation, evidence
        )

    try:
        actual = _digest(path)
    except FileNotFoundError:
        return record("absent", "not_verified", "file_absent")
    except OSError:
        return record("unknown", "not_checked", "file_not_a_readable_regular_file")
    evidence["observed_sha256"] = actual
    if actual != sha256:
        return record("observed", "not_verified", "content_differs")
    if not tests:
        return record("observed", "verified", "content_matches")
    result, observation = _run_tests(tests, cwd or path.parent, timeout)
    return record("observed", result, observation)


def _git(repo: Path, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(  # noqa: S603  # nosec B603 B607 # fixed git argv, no shell
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
        check=False,
    )


def remote_head(listing: str, branch: str) -> tuple[str, str]:
    """Read the head of the exact ref ``refs/heads/{branch}`` from ``git ls-remote``.

    ``git ls-remote`` matches a pattern at the tail of a ref after a ``/``, so
    the listing may also contain another ref such as ``refs/heads/x/refs/heads/{branch}``.
    That line is not this branch. The ids are taken by
    :func:`synapse_channel.result_verification_attempt.listed_refs`, which keys
    the exact ref and ignores every other name.

    Parameters
    ----------
    listing : str
        Standard output of ``git ls-remote --heads -- REMOTE refs/heads/BRANCH``.
    branch : str
        The branch asked for.

    Returns
    -------
    tuple[str, str]
        ``("found", oid)`` when ``refs/heads/{branch}`` is listed once with a
        40-hex id; ``("absent", "")`` when that exact ref is not listed;
        ``("unrecognised", "")`` when a line is not a tab-separated 40-hex
        answer or the exact ref is listed twice.
    """
    ref = f"refs/heads/{branch}"
    found = listed_refs(listing, (ref,), 40)
    if found is None:
        return "unrecognised", ""
    oid = found.get(ref)
    if oid is None:
        return "absent", ""
    return "found", oid


def verify_git_publication(
    repo: Path,
    remote: str,
    branch: str,
    commit: str,
    *,
    operation_id: str,
    communication: str = "not_supplied",
    timeout: float = 60.0,
    attempt: AttemptCheck | None = None,
) -> ResultVerification:
    """Verify that ``commit`` is on ``branch`` of ``remote``.

    The remote branch head is read with ``git ls-remote`` and keyed as the
    exact ref ``refs/heads/{branch}``. Another ref whose name only ends in
    that pattern does not hide the branch or stand in for it. The result is
    verified when the head is the commit, or when the head is present locally
    and contains the commit. Nothing is fetched or pushed. When the head is
    not available locally, the result is ``not_checked``: fetch it, then
    verify again.

    With ``attempt``, the same listing also reads the attempt's marker, and the
    record follows the attempt's reading
    (:data:`~synapse_channel.result_verification_attempt.ATTEMPT_ROWS`); the
    commit and base may then be of a SHA-256 repository. Without it the record
    is exactly what it was before attempts existed.

    Parameters
    ----------
    repo : pathlib.Path
        Local repository that knows ``remote``.
    remote : str
        Remote name or URL.
    branch : str
        Branch name on the remote.
    commit : str
        Full 40-hex commit identity that was to be published.
    operation_id : str
        The operation being verified.
    communication : str, optional
        The boundary state from the outcome report, or ``not_supplied``.
    timeout : float, optional
        Seconds each git call may take.
    attempt : AttemptCheck or None, optional
        The attempt whose marker is read beside the branch.

    Returns
    -------
    ResultVerification
        ``verified`` only when the remote branch contains the commit, and, with
        ``attempt``, only over that attempt's ``applied`` marker.

    Raises
    ------
    ValueError
        On a malformed commit, base, branch, remote or attempt identifier, a
        non-positive timeout, or, with ``attempt``, a commit and base not of the
        repository's object format.
    """
    if attempt is not None:
        return _verify_attempt(
            repo, remote, branch, commit, attempt, operation_id, communication, timeout
        )
    if not _HEX40.fullmatch(commit):
        raise ValueError("the commit is a full 40-hex identity")
    _check_target(branch, remote, timeout)
    evidence = {"remote": remote, "branch": branch, "requested_commit": commit}

    def record(effect: str, result: str, observation: str) -> ResultVerification:
        return ResultVerification(
            operation_id, "git_publication", communication, effect, result, observation, evidence
        )

    try:
        listed = _git(
            repo, "ls-remote", "--heads", "--", remote, f"refs/heads/{branch}", timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return record("unknown", "not_checked", "remote_not_reachable")
    if listed.returncode != 0:
        return record("unknown", "not_checked", "remote_not_reachable")
    status, head = remote_head(listed.stdout, branch)
    if status == "absent":
        return record("absent", "not_verified", "branch_absent_on_remote")
    if status == "unrecognised":
        return record("unknown", "not_checked", "remote_answer_not_recognised")
    evidence["remote_head"] = head
    if head == commit:
        return record("observed", "verified", "remote_head_is_the_commit")
    present = _git(repo, "cat-file", "-e", f"{head}^{{commit}}", timeout=timeout)
    if present.returncode != 0:
        return record("observed", "not_checked", "remote_head_not_available_locally")
    contained = _git(repo, "merge-base", "--is-ancestor", commit, head, timeout=timeout)
    if contained.returncode == 0:
        return record("observed", "verified", "remote_head_contains_the_commit")
    if contained.returncode == 1:
        return record("observed", "not_verified", "remote_head_does_not_contain_the_commit")
    return record("observed", "not_checked", "commit_not_available_locally")


def _check_target(branch: str, remote: str, timeout: float) -> None:
    """Refuse a branch or remote that is not a plain name, and an unusable timeout."""
    if not _BRANCH.fullmatch(branch) or ".." in branch or not remote or remote.startswith("-"):
        raise ValueError("the branch or remote is not a plain name")
    if not 0.0 < timeout < float("inf"):
        raise ValueError("the git timeout is a positive, finite number of seconds")


def _contains(repo: Path, head: str | None, commit: str, timeout: float) -> bool | None:
    """Return whether ``head`` contains ``commit``; ``None`` when it cannot be read here."""
    if head is None:
        return False
    if head == commit:
        return True
    if _git(repo, "cat-file", "-e", f"{head}^{{commit}}", timeout=timeout).returncode != 0:
        return None
    contained = _git(repo, "merge-base", "--is-ancestor", commit, head, timeout=timeout)
    return {0: True, 1: False}.get(contained.returncode)


def _verify_attempt(
    repo: Path,
    remote: str,
    branch: str,
    commit: str,
    attempt: AttemptCheck,
    operation_id: str,
    communication: str,
    timeout: float,
) -> ResultVerification:
    """Verify a publication by the reading of its attempt's branch and marker."""
    length = identifier_length(commit, attempt.base)
    marker = attempt.marker(operation_id)
    _check_target(branch, remote, timeout)
    branch_ref = f"refs/heads/{branch}"
    evidence = {
        "remote": remote,
        "branch": branch,
        "requested_commit": commit,
        "base": attempt.base,
        "attempt_id": attempt.attempt_id,
        "marker_ref": marker.ref,
    }

    def record(effect: str, result: str, observed: str) -> ResultVerification:
        return ResultVerification(
            operation_id, "git_publication", communication, effect, result, observed, evidence
        )

    try:
        shown = _git(repo, "rev-parse", "--show-object-format", timeout=timeout)
        listed = _git(repo, "ls-remote", "--", remote, branch_ref, marker.ref, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return record("unknown", "not_checked", "remote_not_reachable")
    object_format = shown.stdout.strip()
    if shown.returncode != 0 or object_format not in OBJECT_ID_LENGTHS:
        return record("unknown", "not_checked", "object_format_not_read")
    if OBJECT_ID_LENGTHS[object_format] != length:
        raise ValueError(f"the commit and the base are not {object_format} object ids")
    evidence["object_format"] = object_format
    if listed.returncode != 0:
        return record("unknown", "not_checked", "remote_not_reachable")
    refs = listed_refs(listed.stdout, (branch_ref, marker.ref), length)
    if refs is None:
        return record("unknown", "not_checked", "remote_answer_not_recognised")
    head, marker_id = refs.get(branch_ref), refs.get(marker.ref)
    evidence["marker_object"] = marker_id or ""
    if head is not None:
        evidence["remote_head"] = head
    reading = observation(
        branch=head,
        marker=marker_id,
        objects=marker.objects(object_format),
        base=attempt.base,
        candidate=commit,
        contains_candidate=_contains(repo, head, commit, timeout),
    )
    return record(*ATTEMPT_ROWS[reading])
