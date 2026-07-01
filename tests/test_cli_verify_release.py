# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the verify-release CLI
"""CLI tests for verified release receipt generation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel import cli, cli_verify_release
from synapse_channel.core.persistence import EventStore

ROOT = Path(__file__).resolve().parents[1]


def test_parser_verify_release_accepts_real_command_and_receipt_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "verify-release",
            "VERIFY",
            "--name",
            "SYNAPSE-CHANNEL/codex-main",
            "--run",
            "python -c 'print(1)'",
            "--artifact",
            "coverage.xml",
            "--output",
            "receipt.json",
            "--signature",
            "signed-by-owner",
        ]
    )

    assert args.task_id == "VERIFY"
    assert args.name == "SYNAPSE-CHANNEL/codex-main"
    assert args.run == ["python -c 'print(1)'"]
    assert args.artifacts == ["coverage.xml"]
    assert args.output == "receipt.json"
    assert args.signature == "signed-by-owner"
    assert args.func is cli_verify_release._cmd_verify_release


def test_cmd_verify_release_writes_output_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "receipt.json"
    args = argparse.Namespace(
        task_id="VERIFY",
        name="SYNAPSE-CHANNEL/codex-main",
        run=[f"{sys.executable} -c \"print('verified')\""],
        artifacts=[],
        output=str(output),
        signature="signed-by-owner",
        merkle_db="",
    )

    assert cli_verify_release._cmd_verify_release(args) == 0

    assert "verified release receipt: " in capsys.readouterr().out
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["known_failures"] == []
    assert receipt["verification"]["git_head"] == ""
    assert receipt["verification"]["signature"] == "signed-by-owner"


def test_cmd_verify_release_prints_json_failure_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        task_id="VERIFY",
        name="SYNAPSE-CHANNEL/codex-main",
        run=[f'{sys.executable} -c "import sys; sys.exit(5)"'],
        artifacts=[],
        output="",
        signature="",
        merkle_db="",
    )

    assert cli_verify_release._cmd_verify_release(args) == 1

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["known_failures"][0].endswith("exit=5")


def test_verify_release_cli_runs_command_and_writes_receipt(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL
    )
    tracked.write_text("after\n", encoding="utf-8")
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("artifact\n", encoding="utf-8")
    output = tmp_path / "receipt.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "verify-release",
            "VERIFY",
            "--name",
            "SYNAPSE-CHANNEL/codex-main",
            "--run",
            f"{sys.executable} -c \"print('verified')\"",
            "--artifact",
            str(artifact),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "verified release receipt: " in proc.stdout
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["task_id"] == "VERIFY"
    assert receipt["owner"] == "SYNAPSE-CHANNEL/codex-main"
    assert receipt["changed_files"] == ["artifact.txt", "tracked.txt"]
    assert receipt["known_failures"] == []
    assert receipt["verification"]["commands"][0]["exit_code"] == 0
    assert receipt["verification"]["artifacts"][0]["path"] == str(artifact)


def test_verify_release_cli_returns_failure_when_observed_command_fails(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "verify-release",
            "VERIFY",
            "--run",
            f'{sys.executable} -c "import sys; sys.exit(3)"',
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 1
    receipt = json.loads(proc.stdout)
    assert receipt["known_failures"][0].endswith("exit=3")


def _seeded_store(path: Path, count: int = 4) -> None:
    store = EventStore(path)
    for i in range(1, count + 1):
        store.append("claim", {"task_id": f"T{i}"}, ts=float(i))
    store.close()


def test_verify_release_cli_commits_the_coordination_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--merkle-db binds the receipt to the exact coordination history."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "hub.db"
    _seeded_store(db)
    output = tmp_path / "receipt.json"

    exit_code = cli.main(
        ["verify-release", "VERIFY", "--merkle-db", str(db), "--output", str(output)]
    )

    assert exit_code == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    merkle = receipt["verification"]["merkle"]
    assert merkle["tree_size"] == 4
    assert merkle["last_seq"] == 4
    assert len(merkle["root"]) == 64
    assert any("merkle root: " in line for line in receipt["evidence"])


def test_verify_release_cli_rejects_a_missing_merkle_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    exit_code = cli.main(["verify-release", "VERIFY", "--merkle-db", str(tmp_path / "absent.db")])
    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err
