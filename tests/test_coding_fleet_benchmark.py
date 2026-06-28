# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the five-agent coding fleet benchmark

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
_BENCH_PATH = _BENCHMARKS / "coding_fleet_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("coding_fleet_benchmark", _BENCH_PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bench
_SPEC.loader.exec_module(bench)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_scenario_uses_five_agents() -> None:
    """The committed scenario should exercise a five-agent edit fleet."""
    scenario = bench.default_scenario()

    assert scenario.agent_count == 5
    assert [attempt.agent for attempt in scenario.attempts] == [
        "planner",
        "api-dev",
        "test-dev",
        "docs-dev",
        "reviewer",
        "api-dev",
        "planner",
    ]


def test_profile_reports_conflict_latency_and_recovery() -> None:
    """The profile should expose collision, latency, release, and replay evidence."""
    summary = bench.profile()

    assert summary["agents"] == 5
    assert summary["attempts"] == 7
    assert summary["granted"] == 5
    assert summary["refused"] == 2
    assert summary["conflict_rate"] == 2 / 7
    assert summary["claim_latency"]["mean_microseconds"] >= 0
    assert summary["claim_latency"]["max_microseconds"] >= 0
    assert summary["release_recovery"]["released"] == 5
    assert summary["release_recovery"]["remaining_claims"] == 0
    assert summary["replay_recovery"]["events"] == 10
    assert summary["replay_recovery"]["replayed_claims"] == 5
    assert summary["replay_recovery"]["replayed_conflicting_claims"] == 0
    assert summary["evidence_class"] == "local_functional_benchmark"


def test_run_writes_json_results(tmp_path: Path) -> None:
    """The benchmark runner should persist exactly the summary it returns."""
    results = tmp_path / "coding-fleet.json"

    summary = bench.run(results)

    written = json.loads(results.read_text(encoding="utf-8"))
    assert written == summary
    assert written["scenario"] == "five_agent_parallel_edit"


def test_main_runs_and_prints_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The command-line entrypoint should write results and print the headline metrics."""
    results = tmp_path / "coding-fleet.json"

    rc = bench.main(["--results", str(results)])

    assert rc == 0
    output = capsys.readouterr().out
    assert "five-agent coding fleet benchmark" in output
    assert "conflict rate: 28.57%" in output
    assert results.exists()


def test_benchmark_is_documented_and_wired_into_make_bench() -> None:
    """Public benchmark docs and the benchmark runner should list the new script."""
    combined_docs = "\n".join(
        [
            (_REPO_ROOT / "benchmarks" / "README.md").read_text(encoding="utf-8"),
            (_REPO_ROOT / "docs" / "benchmarks.md").read_text(encoding="utf-8"),
        ]
    )
    makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "coding_fleet_benchmark.py" in combined_docs
    assert "five-agent" in combined_docs
    assert "local functional benchmark" in combined_docs
    assert "benchmarks/coding_fleet_benchmark.py" in makefile
