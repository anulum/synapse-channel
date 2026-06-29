# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — workflow CLI regressions

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from synapse_channel.cli_workflow import add_parsers

_GOOD = {
    "name": "release",
    "steps": [
        {"id": "build", "title": "Build", "task_class": "ci"},
        {"id": "test", "title": "Test", "depends_on": ["build"]},
    ],
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser


def _write(tmp_path: Path, data: object) -> str:
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_validate_accepts_a_good_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "validate", _write(tmp_path, _GOOD)])
    assert args.func(args) == 0
    assert "release" in capsys.readouterr().out


def test_validate_rejects_a_cycle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cyclic = {
        "name": "w",
        "steps": [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}],
    }
    parser = _parser()
    args = parser.parse_args(["workflow", "validate", _write(tmp_path, cyclic)])
    assert args.func(args) == 2
    assert "cycle" in capsys.readouterr().err


def test_validate_reports_a_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "validate", "/no/such/workflow.json"])
    assert args.func(args) == 2
    assert "could not read" in capsys.readouterr().err


def test_validate_reports_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    parser = _parser()
    args = parser.parse_args(["workflow", "validate", str(path)])
    assert args.func(args) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_compile_human_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "compile", _write(tmp_path, _GOOD)])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "release/build [ci] <- (none)" in out
    assert "release/test <- release/build" in out


def test_compile_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "compile", "--json", _write(tmp_path, _GOOD)])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["task_id"] == "release/build"
    assert payload[0]["task_class"] == "ci"
    assert payload[1]["depends_on"] == ["release/build"]


def test_compile_reports_a_malformed_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "compile", _write(tmp_path, {"name": "w"})])
    assert args.func(args) == 2
    assert "steps" in capsys.readouterr().err


def _write_named(tmp_path: Path, name: str, data: object) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_plan_routes_ready_tasks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wf = _write(tmp_path, _GOOD)
    status = _write_named(tmp_path, "status.json", {"release/build": "done"})
    agents = _write_named(tmp_path, "agents.json", {"alpha": ["ci"]})
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", wf, "--status", status, "--agents", agents])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "1 done" in out
    assert "release/test -> alpha" in out


def test_plan_json_with_no_files_uses_defaults(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD), "--json"])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"]["ready"] == ["release/build"]
    assert payload["plan"] == []  # no agents available


def test_plan_reports_no_assignments(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD)])
    assert args.func(args) == 0
    assert "no assignments" in capsys.readouterr().out


def test_plan_rejects_a_non_object_status_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _write_named(tmp_path, "status.json", [1, 2])
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD), "--status", status])
    assert args.func(args) == 2
    assert "status file must be a JSON object" in capsys.readouterr().err


def test_plan_rejects_a_non_object_agents_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agents = _write_named(tmp_path, "agents.json", "nope")
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD), "--agents", agents])
    assert args.func(args) == 2
    assert "agents file must be a JSON object" in capsys.readouterr().err


def test_plan_rejects_agent_without_a_class_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agents = _write_named(tmp_path, "agents.json", {"alpha": "ci"})
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD), "--agents", agents])
    assert args.func(args) == 2
    assert "list of task classes" in capsys.readouterr().err
