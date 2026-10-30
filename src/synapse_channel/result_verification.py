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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from synapse_channel.execution_outcome import OUTCOME_SCHEMA, OutcomeState

SCHEMA = "synapse.result-verification.v1"
"""Version of the result-verification record."""

EFFECTS = frozenset({"observed", "absent", "unknown"})
RESULTS = frozenset({"verified", "not_verified", "not_checked"})
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_READ_CHUNK = 1 << 20
_NOFOLLOW: int | None = getattr(os, "O_NOFOLLOW", None)


@dataclass(frozen=True, slots=True)
class ResultVerification:
    """One verifier's account of communication, effect and result, kept apart."""

    operation_id: str
    verifier: str
    communication: str
    effect: str
    result: str
    observation: str
    evidence: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse combinations that would blur the three facts."""
        if self.effect not in EFFECTS or self.result not in RESULTS:
            raise ValueError("unknown effect or result state")
        if self.result == "verified" and self.effect != "observed":
            raise ValueError("a result is verified only from an observed effect")

    @property
    def verified(self) -> bool:
        """Return whether the requested result itself was verified."""
        return self.result == "verified"

    def to_json(self) -> dict[str, object]:
        """Return the JSON record, schema first-class and evidence copied."""
        return {
            "schema": SCHEMA,
            "operation_id": self.operation_id,
            "verifier": self.verifier,
            "communication": self.communication,
            "effect": self.effect,
            "result": self.result,
            "observation": self.observation,
            "evidence": dict(self.evidence),
        }


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
    if _NOFOLLOW is None:
        raise PermissionError("this platform cannot open a file without following a final link")
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
    """Read the head of ``refs/heads/{branch}`` from ``git ls-remote`` output.

    Parameters
    ----------
    listing : str
        Standard output of ``git ls-remote --heads -- REMOTE refs/heads/BRANCH``.
    branch : str
        The branch asked for.

    Returns
    -------
    tuple[str, str]
        ``("found", oid)`` for exactly one line whose oid is 40 lower-case hex
        and whose ref is exactly ``refs/heads/{branch}``; ``("absent", "")``
        for empty output; ``("unrecognised", "")`` for anything else.
    """
    lines = [line for line in listing.splitlines() if line.strip()]
    if not lines:
        return "absent", ""
    if len(lines) != 1:
        return "unrecognised", ""
    oid, separator, ref = lines[0].partition("\t")
    if not separator or ref != f"refs/heads/{branch}" or not _HEX40.fullmatch(oid):
        return "unrecognised", ""
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
) -> ResultVerification:
    """Verify that ``commit`` is on ``branch`` of ``remote``.

    The remote branch head is read with ``git ls-remote``. The result is
    verified when the head is the commit, or when the head is present locally
    and contains the commit. Nothing is fetched or pushed. When the head is
    not available locally, the result is ``not_checked``: fetch it, then
    verify again.

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

    Returns
    -------
    ResultVerification
        ``verified`` only when the remote branch contains the commit.

    Raises
    ------
    ValueError
        On a malformed commit, branch or remote, or a non-positive timeout.
    """
    if not _HEX40.fullmatch(commit):
        raise ValueError("the commit is a full 40-hex identity")
    if not _BRANCH.fullmatch(branch) or ".." in branch or not remote or remote.startswith("-"):
        raise ValueError("the branch or remote is not a plain name")
    if not 0.0 < timeout < float("inf"):
        raise ValueError("the git timeout is a positive, finite number of seconds")
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
