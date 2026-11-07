# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the stage profile of the claim-grant and mailbox hot paths
"""The hot-path profile assigns every function to one stage and reports honest shares."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks/hot_path_profile.py"
RESULT = Path(__file__).resolve().parents[1] / "benchmarks/results/hot_path_profile.json"


def _module() -> dict[str, Any]:
    return runpy.run_path(str(BENCHMARK), run_name="hot_path_profile_api")


@pytest.mark.parametrize(
    ("filename", "function", "stage"),
    [
        ("~", "<method 'commit' of 'sqlite3.Connection' objects>", "sqlite durable commit"),
        ("~", "<method 'execute' of 'sqlite3.Connection' objects>", "sqlite statements"),
        ("~", "<built-in method posix.fsync>", "fsync"),
        ("~", "<method 'recv' of '_socket.socket' objects>", "socket io"),
        ("~", "<method 'poll' of 'select.epoll' objects>", "socket io"),
        ("~", "<built-in method builtins.len>", "other"),
        ("/venv/site-packages/websockets/frames.py", "parse", "websocket framing"),
        ("/usr/lib/python3.12/json/encoder.py", "iterencode", "json"),
        ("/src/synapse_channel/client/agent.py", "send_message", "client logic"),
        ("/src/synapse_channel/core/hub.py", "handle", "hub logic"),
        ("/usr/lib/python3.12/asyncio/base_events.py", "_run_once", "asyncio"),
        ("/usr/lib/python3.12/sqlite3/__init__.py", "adapt", "sqlite statements"),
        ("C:\\Python312\\Lib\\copy.py", "deepcopy", "other"),
    ],
)
def test_each_function_lands_in_one_fixed_stage(filename: str, function: str, stage: str) -> None:
    classify: Callable[[str, str], str] = _module()["classify"]
    assert classify(filename, function) == stage


def test_a_short_run_reports_shares_that_add_up(tmp_path: Path) -> None:
    output = tmp_path / "profile.json"
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            str(BENCHMARK),
            "--runs",
            "2",
            "--chats",
            "2",
            "--lease-cycles",
            "1",
            "--results",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    measured = json.loads(output.read_text(encoding="utf-8"))
    assert measured["runs"] == 2 and len(measured["per_run"]) == 2
    for run in measured["per_run"]:
        assert sum(run["stage_share"].values()) == pytest.approx(1.0)
        assert run["durable_commits_per_operation"] > 0
        assert run["claim_p50_us"] > 0 and run["chat_receipt_p50_us"] > 0
    assert 0 < measured["median_python_share"] < 1
    assert "shared host" in measured["host"]["isolation"]
    assert json.loads(process.stdout) == measured["median_stage_share"]


def test_zero_work_is_refused_before_any_result_is_written(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist.json"
    process = subprocess.run(
        [sys.executable, "-B", str(BENCHMARK), "--runs", "0", "--results", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 2 and "count must be positive" in process.stderr
    assert not output.exists()


def test_the_committed_result_backs_the_documented_decision() -> None:
    measured = json.loads(RESULT.read_text(encoding="utf-8"))
    share = measured["median_stage_share"]
    assert set(share) == set(_module()["STAGES"])
    # The decision rests on durable commits outweighing all Python code, and
    # "other" (the rest of the standard library) counts as Python for the bound.
    bound = measured["median_python_share"] + share["other"]
    assert share["sqlite durable commit"] > bound
    docs = (Path(__file__).resolve().parents[1] / "docs/benchmarks.md").read_text("utf-8")
    assert f"{100 * share['sqlite durable commit']:.1f} %" in docs
    assert f"{100 * measured['median_python_share']:.1f} %" in docs
    assert f"at most {100 * bound:.1f} %" in docs
    assert f"median was {measured['median_durable_commits_per_operation']:.2f}" in docs
