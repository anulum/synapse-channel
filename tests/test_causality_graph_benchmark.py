# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — causality graph benchmark entry point
"""The benchmark times real graph builds and refuses a disagreeing baseline."""

from __future__ import annotations

import importlib.util
import json
import runpy
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from synapse_channel.core.causality import CausalNode

_PATH = Path(__file__).resolve().parents[1] / "benchmarks/causality_graph_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("causality_graph_benchmark", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def test_the_cli_writes_matching_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The executable entry measures each size and writes the printed document."""
    results = tmp_path / "out.json"
    previous = sys.argv
    sys.argv = [
        str(_PATH),
        "--claims",
        "60",
        "120",
        "--worktrees",
        "3",
        "--seed",
        "2",
        "--results",
        str(results),
    ]
    try:
        with pytest.raises(SystemExit) as completed:
            runpy.run_path(str(_PATH), run_name="__main__")
    finally:
        sys.argv = previous
    assert completed.value.code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(results.read_text(encoding="utf-8"))
    first, second = printed["runs"]
    assert (first["claims"], first["events"], second["events"]) == (60, 120, 240)
    assert first["contention_edges"] > 0 and second["contention_edges"] > 0
    assert first["indexed_build_s"] >= 0 and first["exhaustive_scan_s"] >= 0


def test_a_run_without_results_prints_only(capsys: pytest.CaptureFixture[str]) -> None:
    """Without ``--results`` the document is printed and nothing is written."""
    assert bench.main(["--claims", "20", "--worktrees", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["claims"] == 20


def test_a_disagreeing_baseline_fails_the_run() -> None:
    """A baseline that misses one contention edge makes the measurement fail."""

    def missing_one(nodes: Sequence[CausalNode]) -> set[tuple[int, int]]:
        pairs = bench.exhaustive_contention(nodes)
        return set(sorted(pairs)[1:])

    with pytest.raises(RuntimeError, match="differ"):
        bench.measure(80, 2, 5, reference=missing_one)


@pytest.mark.parametrize(("claims", "worktrees"), [(0, 1), (1, 0)])
def test_an_empty_fixture_is_refused(claims: int, worktrees: int) -> None:
    """Zero claims or worktrees cannot produce a measurement."""
    with pytest.raises(ValueError, match="positive"):
        bench.synthetic_log(claims, worktrees, 1)
