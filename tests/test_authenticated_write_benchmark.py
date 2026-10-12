# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real authenticated-write benchmark entry point
"""Verify measured counts and authentication through the runnable CLI."""

from __future__ import annotations

import importlib.util
import json
import runpy
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "benchmarks/authenticated_write_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("authenticated_write_benchmark", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def test_real_cli_counts_and_authenticates_both_durability_modes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The executable entry reports only rows actually committed and authenticated."""
    previous = sys.argv
    sys.argv = [str(_PATH), "--normal-count", "3", "--durable-count", "2"]
    try:
        with pytest.raises(SystemExit) as completed:
            runpy.run_path(str(_PATH), run_name="__main__")
    finally:
        sys.argv = previous
    assert completed.value.code == 0
    normal, durable = json.loads(capsys.readouterr().out)
    assert normal["count"] == normal["authenticated_rows_verified"] == 3
    assert durable["count"] == durable["authenticated_rows_verified"] == 2
    assert normal["durable"] is False and durable["durable"] is True
    assert normal["p50_us"] > 0 and durable["p50_us"] > 0


def test_an_empty_workload_cannot_publish_latency() -> None:
    """Reject an empty workload rather than reporting fabricated statistics."""
    with pytest.raises(ValueError, match="positive"):
        bench.measure(0, durable=False)
