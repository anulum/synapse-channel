# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a successful exchange with a wrong effect is not a verified result
"""Result verifiers check the world, not the reply.

Everything here is real: files on disk, git repositories with a local bare
remote, test commands, and a worker run through ``synapse worker-session``
whose process exits 0. The acceptance case shows a worker that reports
``confirmed_done`` while writing the wrong content. Its result is reported
``not_verified``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.cli import main
from synapse_channel.result_verification import (
    SCHEMA,
    ResultVerification,
    read_outcome,
    remote_head,
    verify_file_change,
    verify_git_publication,
)

GOOD = b"requested content\n"
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()
ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    return done.stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def published(tmp_path: Path) -> tuple[Path, Path]:
    """A work repository with one commit pushed to a local bare remote."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(work, "remote", "add", "origin", str(remote))
    _commit(work, "first")
    _git(work, "push", "-q", "origin", "main")
    return work, remote


def test_a_worker_that_exits_zero_with_wrong_content_is_not_verified(tmp_path: Path) -> None:
    """Acceptance: ``confirmed_done`` at the worker boundary, wrong effect, not verified."""
    target = tmp_path / "result.txt"
    report = tmp_path / "outcome.json"
    worker = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "worker-session",
            "--identity",
            "PROJECT/worker",
            "--outcome-file",
            str(report),
            "--",
            sys.executable,
            "-c",
            f"open({str(target)!r}, 'w').write('wrong content')",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        check=False,
    )
    assert worker.returncode == 0
    operation_id, state = read_outcome(report)
    assert state == "confirmed_done"

    verdict = verify_file_change(
        target, GOOD_SHA, operation_id=operation_id, communication=state
    ).to_json()

    assert verdict["communication"] == "confirmed_done"
    assert (verdict["effect"], verdict["result"]) == ("observed", "not_verified")
    assert verdict["observation"] == "content_differs"


def test_file_change_states(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    absent = verify_file_change(path, GOOD_SHA, operation_id="op")
    path.write_bytes(GOOD)
    exact = verify_file_change(path, GOOD_SHA, operation_id="op")
    path.write_bytes(b"other")
    wrong = verify_file_change(path, GOOD_SHA, operation_id="op")

    assert (absent.effect, absent.result, absent.observation) == (
        "absent",
        "not_verified",
        "file_absent",
    )
    assert (exact.effect, exact.result, exact.verified) == ("observed", "verified", True)
    assert exact.evidence["observed_sha256"] == GOOD_SHA
    assert (wrong.effect, wrong.result) == ("observed", "not_verified")


@pytest.mark.parametrize(
    ("tests", "timeout", "result", "observation"),
    [
        (
            [sys.executable, "-c", "raise SystemExit(0)"],
            30.0,
            "verified",
            "content_matches_and_tests_passed",
        ),
        (
            [sys.executable, "-c", "raise SystemExit(4)"],
            30.0,
            "not_verified",
            "tests_failed_with_status_4",
        ),
        (
            [sys.executable, "-c", "import time; time.sleep(5)"],
            0.2,
            "not_checked",
            "tests_timed_out",
        ),
        (["/nonexistent/test-runner"], 30.0, "not_checked", "tests_could_not_start"),
    ],
)
def test_matching_content_also_needs_its_tests(
    tmp_path: Path, tests: list[str], timeout: float, result: str, observation: str
) -> None:
    path = tmp_path / "out.txt"
    path.write_bytes(GOOD)

    verdict = verify_file_change(path, GOOD_SHA, operation_id="op", tests=tests, timeout=timeout)

    assert (verdict.result, verdict.observation) == (result, observation)


def test_tests_are_not_run_for_wrong_content(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    marker = tmp_path / "ran"
    path.write_bytes(b"other")

    verify_file_change(
        path,
        GOOD_SHA,
        operation_id="op",
        tests=[sys.executable, "-c", f"open({str(marker)!r}, 'w')"],
    )

    assert not marker.exists()


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_a_path_that_is_not_a_regular_file_cannot_be_checked(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "out.txt"
    if kind == "symlink":
        (tmp_path / "real.txt").write_bytes(GOOD)
        path.symlink_to(tmp_path / "real.txt")
    else:
        path.mkdir()

    verdict = verify_file_change(path, GOOD_SHA, operation_id="op")

    assert (verdict.effect, verdict.result) == ("unknown", "not_checked")


@pytest.mark.parametrize(
    ("digest", "timeout", "message"),
    [("ABC", 1.0, "64 lower-case hex"), (GOOD_SHA, 0.0, "positive, finite")],
)
def test_file_change_input_is_validated(
    tmp_path: Path, digest: str, timeout: float, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        verify_file_change(tmp_path / "x", digest, operation_id="op", timeout=timeout)


def test_git_publication_states(published: tuple[Path, Path], tmp_path: Path) -> None:
    """Head equal, head containing, head not containing, branch absent, unreachable."""
    work, remote = published
    first = _git(work, "rev-parse", "HEAD")

    exact = verify_git_publication(work, "origin", "main", first, operation_id="op")
    second = _commit(work, "second")
    unpublished = verify_git_publication(work, "origin", "main", second, operation_id="op")
    _git(work, "push", "-q", "origin", "main")
    contained = verify_git_publication(work, "origin", "main", first, operation_id="op")
    _git(work, "checkout", "-q", "-b", "side", first)
    side = _commit(work, "side")
    elsewhere = verify_git_publication(work, "origin", "main", side, operation_id="op")
    absent = verify_git_publication(work, "origin", "missing", first, operation_id="op")
    unreachable = verify_git_publication(
        work, str(tmp_path / "no-such-remote"), "main", first, operation_id="op"
    )

    assert (exact.result, exact.observation) == ("verified", "remote_head_is_the_commit")
    assert (unpublished.effect, unpublished.result) == ("observed", "not_verified")
    assert (contained.result, contained.observation) == (
        "verified",
        "remote_head_contains_the_commit",
    )
    assert elsewhere.result == "not_verified"
    assert (absent.effect, absent.result) == ("absent", "not_verified")
    assert (unreachable.effect, unreachable.result) == ("unknown", "not_checked")


def test_git_publication_without_local_objects_is_not_checked(
    published: tuple[Path, Path], tmp_path: Path
) -> None:
    """A remote head or commit this repository has not fetched cannot be decided."""
    work, remote = published
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    _commit(other, "elsewhere")
    _git(other, "push", "-q", "origin", "main")
    first = _git(work, "rev-parse", "HEAD")

    head_missing = verify_git_publication(work, "origin", "main", first, operation_id="op")
    _git(work, "fetch", "-q", "origin")
    commit_missing = verify_git_publication(work, "origin", "main", "e" * 40, operation_id="op")

    assert (head_missing.effect, head_missing.result, head_missing.observation) == (
        "observed",
        "not_checked",
        "remote_head_not_available_locally",
    )
    assert commit_missing.observation == "commit_not_available_locally"


def test_git_unavailable_is_not_checked(
    published: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a git executable on PATH the verifier cannot look."""
    work, _remote = published
    first = _git(work, "rev-parse", "HEAD")
    monkeypatch.setenv("PATH", "/nonexistent")

    verdict = verify_git_publication(work, "origin", "main", first, operation_id="op")

    assert (verdict.effect, verdict.result) == ("unknown", "not_checked")


@pytest.mark.parametrize(
    ("remote", "branch", "commit", "timeout", "message"),
    [
        ("origin", "main", "abc", 1.0, "full 40-hex"),
        ("origin", "../main", "a" * 40, 1.0, "plain name"),
        ("--upload-pack=x", "main", "a" * 40, 1.0, "plain name"),
        ("", "main", "a" * 40, 1.0, "plain name"),
        ("origin", "main", "a" * 40, -1.0, "positive, finite"),
    ],
)
def test_git_publication_input_is_validated(
    tmp_path: Path, remote: str, branch: str, commit: str, timeout: float, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        verify_git_publication(tmp_path, remote, branch, commit, operation_id="op", timeout=timeout)


def test_a_record_cannot_claim_a_verified_result_without_an_observed_effect() -> None:
    with pytest.raises(ValueError, match="only from an observed effect"):
        ResultVerification("op", "file_change", "not_supplied", "absent", "verified", "x")
    with pytest.raises(ValueError, match="unknown effect or result"):
        ResultVerification("op", "file_change", "not_supplied", "seen", "verified", "x")
    record = ResultVerification("op", "x", "not_supplied", "unknown", "not_checked", "y")
    assert record.to_json()["schema"] == SCHEMA


@pytest.mark.parametrize(
    "content",
    [
        "{",
        json.dumps({"schema": "other"}),
        json.dumps(
            {
                "schema": "synapse.execution-outcome.v1",
                "operation_id": "",
                "state": "confirmed_done",
            }
        ),
        "[]",
    ],
)
def test_only_execution_outcome_reports_are_read(tmp_path: Path, content: str) -> None:
    path = tmp_path / "outcome.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="outcome report"):
        read_outcome(path)


def test_the_cli_reports_and_exits_by_result(
    tmp_path: Path, published: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """``synapse verify-result`` prints one record; 0 verified, 1 not, 3 undecided, 2 refused."""
    path = tmp_path / "out.txt"
    path.write_bytes(GOOD)
    outcome = tmp_path / "outcome.json"
    outcome.write_text(
        json.dumps(
            {
                "schema": "synapse.execution-outcome.v1",
                "operation_id": "w-1",
                "state": "confirmed_done",
            }
        ),
        encoding="utf-8",
    )
    work, _remote = published
    first = _git(work, "rev-parse", "HEAD")

    assert (
        main(
            [
                "verify-result",
                "file",
                "--path",
                str(path),
                "--sha256",
                GOOD_SHA,
                "--outcome",
                str(outcome),
            ]
        )
        == 0
    )
    record = json.loads(capsys.readouterr().out)
    assert (record["operation_id"], record["communication"], record["result"]) == (
        "w-1",
        "confirmed_done",
        "verified",
    )
    assert (
        main(
            [
                "verify-result",
                "file",
                "--path",
                str(tmp_path / "gone"),
                "--sha256",
                GOOD_SHA,
                "--operation-id",
                "op",
            ]
        )
        == 1
    )
    capsys.readouterr()
    assert (
        main(
            [
                "verify-result",
                "file",
                "--path",
                str(path),
                "--sha256",
                GOOD_SHA,
                "--operation-id",
                "op",
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(0)",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "verify-result",
                "git",
                "--repo",
                str(work),
                "--remote",
                "origin",
                "--branch",
                "main",
                "--commit",
                first,
                "--operation-id",
                "op",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "verify-result",
                "git",
                "--repo",
                str(work),
                "--remote",
                str(tmp_path / "none"),
                "--branch",
                "main",
                "--commit",
                first,
                "--operation-id",
                "op",
            ]
        )
        == 3
    )
    capsys.readouterr()

    assert main(["verify-result", "file", "--path", str(path), "--sha256", GOOD_SHA]) == 2
    assert "give --operation-id or --outcome" in capsys.readouterr().err
    assert (
        main(
            [
                "verify-result",
                "file",
                "--path",
                str(path),
                "--sha256",
                GOOD_SHA,
                "--outcome",
                str(outcome),
                "--operation-id",
                "other",
            ]
        )
        == 2
    )
    assert "differs from the outcome report" in capsys.readouterr().err
    assert (
        main(
            [
                "verify-result",
                "file",
                "--path",
                str(path),
                "--sha256",
                GOOD_SHA,
                "--outcome",
                str(tmp_path / "missing.json"),
            ]
        )
        == 2
    )


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ("", ("absent", "")),
        (f"{'a' * 40}\trefs/heads/main\n", ("found", "a" * 40)),
        ("--upload-pack=id\trefs/heads/main\n", ("unrecognised", "")),
        (f"{'A' * 40}\trefs/heads/main\n", ("unrecognised", "")),
        (f"{'a' * 40}\trefs/heads/main2\n", ("unrecognised", "")),
        (f"{'a' * 40} refs/heads/main\n", ("unrecognised", "")),
        (f"{'a' * 40}\trefs/heads/main\n{'b' * 40}\trefs/heads/main\n", ("unrecognised", "")),
    ],
)
def test_only_an_exact_remote_head_line_is_accepted(
    listing: str, expected: tuple[str, str]
) -> None:
    """An option-shaped, uppercase, foreign-ref, untabbed or doubled answer is not a head."""
    assert remote_head(listing, "main") == expected


def test_a_platform_that_cannot_refuse_links_does_not_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without O_NOFOLLOW the file is not opened at all."""
    import synapse_channel.result_verification as module

    path = tmp_path / "out.txt"
    path.write_bytes(GOOD)
    monkeypatch.setattr(module, "_NOFOLLOW", None)

    verdict = verify_file_change(path, GOOD_SHA, operation_id="op")

    assert (verdict.effect, verdict.result) == ("unknown", "not_checked")


def test_an_ambiguous_remote_answer_is_not_checked(published: tuple[Path, Path]) -> None:
    """git matches ref patterns from the end, so a branch ending in the name doubles the answer."""
    work, _remote = published
    first = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "-q", "origin", "main:refs/heads/feature/refs/heads/main")

    verdict = verify_git_publication(work, "origin", "main", first, operation_id="op")

    assert (verdict.effect, verdict.result, verdict.observation) == (
        "unknown",
        "not_checked",
        "remote_answer_not_recognised",
    )
