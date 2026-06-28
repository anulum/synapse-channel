# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — regression tests for verify-release review-finding fixes

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.core import release_verification as rv


def _run(argv: list[str], timeout: float = 30.0) -> dict[str, object]:
    return dict(rv._run_command(argv, cwd=None, timeout_seconds=timeout))


def test_empty_command_is_recorded_as_a_failure_not_a_crash() -> None:
    evidence = _run([])
    assert evidence["exit_code"] == -1
    assert evidence["argv"] == []


def test_missing_command_is_recorded_as_a_failure() -> None:
    evidence = _run(["synapse-no-such-program-xyz"])
    assert evidence["exit_code"] == -1


def test_command_timeout_is_recorded_as_a_failure() -> None:
    evidence = _run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
    assert evidence["exit_code"] == -1


def test_successful_command_records_zero_exit() -> None:
    evidence = _run([sys.executable, "-c", "print('ok')"])
    assert evidence["exit_code"] == 0


def test_hash_artifact_streams_and_handles_absent_or_directory(tmp_path: Path) -> None:
    big = tmp_path / "artifact.bin"
    big.write_bytes(b"a" * (rv._HASH_CHUNK_BYTES * 2 + 5))
    result = rv._hash_artifact(big)
    assert result is not None
    assert result["size_bytes"] == rv._HASH_CHUNK_BYTES * 2 + 5
    assert len(result["sha256"]) == 64

    assert rv._hash_artifact(tmp_path / "missing.bin") is None
    assert rv._hash_artifact(tmp_path) is None  # a directory is not a hashable artifact


def test_git_state_outside_a_repo_is_empty(tmp_path: Path) -> None:
    state = rv.collect_git_state(tmp_path)
    assert state.head == ""
    assert state.tree == ""
    assert state.changed_files == []


def test_dirty_tree_is_surfaced_as_non_failing_evidence() -> None:
    receipt = rv.build_verified_release_receipt(
        task_id="T1",
        owner="alice",
        commands=[[sys.executable, "-c", "print('ok')"]],
        artifacts=[],
        changed_files=["src/a.py"],
        git_head="deadbeef",
        git_tree="cafef00d",
    )
    # The dirty-tree caveat is visible evidence, not a known failure (so exit
    # stays clean for the normal pre-commit verify workflow).
    assert any("uncommitted change" in entry for entry in receipt["evidence"])
    assert receipt["known_failures"] == []


def test_failed_command_degrades_the_receipt() -> None:
    receipt = rv.build_verified_release_receipt(
        task_id="T1",
        owner="alice",
        commands=[[sys.executable, "-c", "import sys; sys.exit(3)"]],
        artifacts=[],
        changed_files=[],
        git_head="",
        git_tree="",
    )
    assert any("exit=3" in entry for entry in receipt["known_failures"])


def test_cli_writes_an_owner_only_atomic_receipt(tmp_path: Path) -> None:
    from synapse_channel import cli_verify_release

    payload = json.dumps({"task_id": "T1"})
    output = tmp_path / "nested" / "receipt.json"
    cli_verify_release._write_receipt_file(output, payload)
    assert output.exists()
    assert output.stat().st_mode & 0o077 == 0
    assert not list((tmp_path / "nested").glob("*.tmp"))
    assert json.loads(output.read_text())["task_id"] == "T1"


def test_cli_receipt_write_cleans_up_temp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from synapse_channel import cli_verify_release

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("synapse_channel.cli_verify_release.os.replace", _boom)
    output = tmp_path / "receipt.json"
    with pytest.raises(OSError, match="disk full"):
        cli_verify_release._write_receipt_file(output, "{}")
    assert not list(tmp_path.glob("*.tmp"))
    assert not output.exists()


def test_git_stdout_swallows_a_subprocess_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("git not installed")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert rv.collect_git_state(tmp_path).head == ""
