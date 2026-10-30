# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the contract-conflict benchmark measures real merges
"""The benchmark's outcomes come from real merges, independent of the digests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks" / "contract_conflict_benchmark.py"


def _load() -> ModuleType:
    """Import the benchmark script as a module."""
    spec = importlib.util.spec_from_file_location("contract_conflict_benchmark", BENCHMARK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BENCH = _load()

EXPECTED = {
    "removal_vs_usage_built_on_old": "caught",
    "addition_vs_usage_built_on_old": "false_block",
    "whitespace_vs_usage_edit": "false_block",
    "removal_vs_usage_avoiding_the_removed_path": "false_block",
    "removal_without_regeneration": "caught",
    "removal_and_unrelated_docs": "clear",
    "two_independent_contracts": "clear",
    "removal_vs_undeclared_prefix_sibling": "missed",
    "removal_with_malformed_record": "false_block",
    "declaration_dropped_by_branch": "refused",
    "removal_vs_undeclared_client": "missed",
    "composed_removal_vs_deployment_built_on_old": "caught",
    "composed_removal_vs_client_and_deployment_built_on_old": "caught",
    "composed_addition_vs_deployment_scaling": "false_block",
    "removal_vs_undeclared_deployment": "missed",
}


def test_every_scenario_has_its_measured_outcome(tmp_path: Path) -> None:
    """The run writes its record, and each labelled scenario lands where the merge says."""
    results = tmp_path / "results.json"

    summary = BENCH.run(results)

    assert {row["scenario"]: row["outcome"] for row in summary["scenarios"]} == EXPECTED
    assert summary["counts"] == {
        "caught": 4,
        "missed": 3,
        "false_block": 5,
        "clear": 2,
        "refused": 1,
    }
    assert summary["lost_parallelism"] == {"blocked_mergeable": 5, "mergeable": 7}
    assert json.loads(results.read_text(encoding="utf-8"))["counts"] == summary["counts"]


def test_the_oracle_judges_the_merge_not_the_digests(tmp_path: Path) -> None:
    """A merged client or deployment using a removed path breaks; defined paths do not."""
    names = {scenario.name: scenario for scenario in BENCH.SCENARIOS}
    broken = BENCH.build(tmp_path, names["removal_vs_usage_built_on_old"])
    fine = BENCH.build(tmp_path, names["removal_and_unrelated_docs"])
    deployed = BENCH.build(tmp_path, names["removal_vs_undeclared_deployment"])

    assert BENCH.integration_breaks(broken, ["api", "usage"]) is True
    assert BENCH.integration_breaks(fine, ["api", "docs"]) is False
    assert BENCH.integration_breaks(deployed, ["api"]) is False
    assert BENCH.integration_breaks(deployed, ["api", "deploy"]) is True


def test_the_command_line_prints_the_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main`` writes the record where asked and prints only the counts."""
    assert BENCH.main(["--results", str(tmp_path / "out.json")]) == 0
    assert json.loads(capsys.readouterr().out)["caught"] == 4
    assert (tmp_path / "out.json").exists()
    assert BENCH.run(None)["counts"]["refused"] == 1


def test_the_script_runs_as_a_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Running the file itself as ``__main__`` exits 0 and prints the counts."""
    import runpy

    monkeypatch.setattr(sys, "argv", [str(BENCHMARK), "--results", str(tmp_path / "main.json")])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(BENCHMARK), run_name="__main__")

    assert stopped.value.code == 0
    assert json.loads(capsys.readouterr().out)["refused"] == 1
