# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the advisory policy-check CLI

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_policy_check


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    cli_policy_check.add_parsers(sub)
    return parser


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _policy(tmp_path: Path, rules: dict[str, Any], mode: str = "advisory") -> Path:
    return _write(tmp_path / "policy.json", {"version": 1, "mode": mode, "rules": rules})


def _receipt(tmp_path: Path, **fields: Any) -> Path:
    base = {"task_id": "T1", "owner": "alice", "evidence": [], "changed_files": []}
    base.update(fields)
    return _write(tmp_path / "receipt.json", base)


def _run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    return cli_policy_check._cmd_policy_check(args)


def test_text_report_passes_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"required_tests": {"commands": ["pytest"]}})
    receipt = _receipt(tmp_path, evidence=["pytest -q passed"])
    code = _run(["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(receipt)])
    out = capsys.readouterr().out
    assert code == 0
    assert "-> pass" in out
    assert "required_tests" in out


def test_json_report_is_structured(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {"no_merge_without_receipt": {"required": True}})
    receipt = _receipt(tmp_path)
    code = _run(
        [
            "policy-check",
            "TASK-7",
            "--policy",
            str(policy),
            "--receipt-json",
            str(receipt),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0  # advisory: never blocks without --enforce
    assert report["subject"] == "TASK-7"
    assert report["overall"] == "fail"
    assert report["blocked"] is False
    assert report["decisions"][0]["rule"] == "no_merge_without_receipt"


def test_enforce_mode_blocks_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path, {"no_merge_without_receipt": {"required": True}}, mode="enforcement")
    receipt = _receipt(tmp_path)
    code = _run(
        ["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(receipt), "--enforce"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "BLOCKED" in out


def test_enforce_without_failures_returns_zero(tmp_path: Path) -> None:
    policy = _policy(tmp_path, {"no_merge_without_receipt": {"required": True}}, mode="enforcement")
    receipt = _receipt(tmp_path, evidence=["pytest passed"])
    code = _run(
        ["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(receipt), "--enforce"]
    )
    assert code == 0


def test_warn_next_action_is_shown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {"evidence_freshness": {"max_age_seconds": 10}})
    receipt = _receipt(tmp_path, freshness_seconds=99.0)
    _run(["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(receipt)])
    out = capsys.readouterr().out
    assert "next:" in out


def test_missing_policy_file_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    receipt = _receipt(tmp_path)
    code = _run(
        [
            "policy-check",
            "T1",
            "--policy",
            str(tmp_path / "nope.json"),
            "--receipt-json",
            str(receipt),
        ]
    )
    assert code == 2
    assert "policy-check error" in capsys.readouterr().out


def test_missing_receipt_file_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {})
    code = _run(
        ["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(tmp_path / "no.json")]
    )
    assert code == 2
    assert "receipt file does not exist" in capsys.readouterr().out


def test_invalid_receipt_json_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code = _run(["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(bad)])
    assert code == 2
    assert "invalid receipt JSON" in capsys.readouterr().out


def test_non_object_receipt_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = _policy(tmp_path, {})
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    code = _run(["policy-check", "T1", "--policy", str(policy), "--receipt-json", str(arr)])
    assert code == 2
    assert "must be an object" in capsys.readouterr().out
