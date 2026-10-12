# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real handler benchmark CLI and measured evidence

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks/coordination_handlers_benchmark.py"


def test_cli_measures_receipted_chat_and_complete_keyed_leases(tmp_path: Path) -> None:
    output = tmp_path / "measured.json"
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            str(BENCHMARK),
            "--chats",
            "3",
            "--lease-cycles",
            "2",
            "--results",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    measured = json.loads(output.read_text())
    assert measured == json.loads(process.stdout)
    assert measured["chat_events"] == measured["chats"] == 3
    assert measured["claim_events"] == measured["lease_cycles"] == 2
    assert measured["operation_records"] >= 10 and measured["pending_outbox"] == 0
    assert set(measured["leases"]) == {"claim", "task_update", "checkpoint", "handoff", "release"}
    for distribution in [measured["chat"], *measured["leases"].values()]:
        assert 0 < distribution["min_us"] <= distribution["p50_us"] <= distribution["max_us"]
    assert measured["host"]["python"] and "shared host" in measured["host"]["isolation"]


def test_cli_refuses_zero_work_before_opening_the_result_path(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist.json"
    process = subprocess.run(
        [sys.executable, "-B", str(BENCHMARK), "--chats", "0", "--results", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 2 and "count must be positive" in process.stderr
    assert not output.exists()


def test_programmatic_entry_point_returns_after_joining_its_hub(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(BENCHMARK), run_name="benchmark_api")
    main: Callable[[list[str] | None], int] = namespace["main"]

    output = tmp_path / "entry-point.json"
    assert main(["--chats", "1", "--lease-cycles", "1", "--results", str(output)]) == 0
    measured = json.loads(output.read_text())
    assert measured["host"]["pid"] == os.getpid()
    assert measured["chat_events"] == measured["claim_events"] == 1
    assert measured["pending_outbox"] == 0
