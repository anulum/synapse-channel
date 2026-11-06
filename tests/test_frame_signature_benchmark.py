# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the signed-frame benchmark reports only real work
"""Run the signed-frame benchmark at small sizes and check what it reports."""

from __future__ import annotations

import importlib.util
import json
import runpy
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "benchmarks/frame_signature_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("frame_signature_benchmark", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def test_the_cli_reports_admitted_frames_and_granted_claims(
    capsys: pytest.CaptureFixture[str],
) -> None:
    previous = sys.argv
    sys.argv = [str(_PATH), "--gate-count", "5", "--hub-count", "3"]
    try:
        with pytest.raises(SystemExit) as completed:
            runpy.run_path(str(_PATH), run_name="__main__")
    finally:
        sys.argv = previous
    assert completed.value.code == 0
    gate, plain, signed = json.loads(capsys.readouterr().out)
    assert gate["count"] == gate["admitted"] == 5
    assert gate["sign_p50_us"] > 0 and gate["check_p50_us"] > 0
    assert (plain["signed_frames"], plain["granted"]) == (False, 3)
    assert (signed["signed_frames"], signed["granted"]) == (True, 3)
    assert plain["p50_us"] > 0 and signed["p50_us"] > 0


def test_empty_workloads_cannot_publish_numbers() -> None:
    with pytest.raises(ValueError, match="positive"):
        bench.measure_gate(0)
    with pytest.raises(ValueError, match="positive"):
        bench.measure_hub(0, signed=True)
    with pytest.raises(ValueError, match="at most"):
        bench.measure_hub(bench.MAX_CLAIMS_PER_AGENT + 1, signed=False)
