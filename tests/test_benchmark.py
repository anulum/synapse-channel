# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the committed relay token benchmark harness

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_BENCH_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "relay_token_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("relay_token_benchmark", _BENCH_PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def test_get_encoder_returns_real_tokenizer_when_available() -> None:
    encoder, name = bench.get_encoder()
    # tiktoken is a declared benchmark dependency and installed in this env.
    assert encoder is not None
    assert name == bench.ENCODING_NAME
    assert encoder.encode("hello world")  # a real tokeniser yields tokens


def test_get_encoder_falls_back_without_tiktoken(monkeypatch: pytest.MonkeyPatch) -> None:
    # A sys.modules entry of None makes `import tiktoken` raise, exercising the
    # heuristic fallback without uninstalling the dependency.
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    encoder, name = bench.get_encoder()
    assert encoder is None
    assert name == bench.HEURISTIC_NAME


def test_count_tokens_with_encoder() -> None:
    class FakeEncoder:
        def encode(self, text: str) -> list[int]:
            return list(range(len(text.split())))

    assert bench.count_tokens("a b c", FakeEncoder()) == 3


def test_count_tokens_heuristic_handles_empty_and_nonempty() -> None:
    assert bench.count_tokens("", None) == 0
    assert bench.count_tokens("abcd", None) == 1
    assert bench.count_tokens("a" * 12, None) == 3


def test_measure_message_reports_all_serialisations() -> None:
    env = {
        "sender": "A",
        "target": "all",
        "type": "chat",
        "payload": "hello",
        "timestamp": 1.5,
        "msg_id": 1,
        "hub_id": "syn-x",
        "task_id": "T",  # auxiliary field the lite format drops
    }
    measured = bench.measure_message(env, None)
    assert measured["type"] == "chat"
    assert measured["bytes_raw_wire"] >= measured["bytes_lite"]
    assert measured["roundtrip_core_fidelity"] is True
    assert set(measured) == {
        "type",
        "bytes_raw_wire",
        "bytes_raw_core_compact",
        "bytes_lite",
        "tokens_raw_wire",
        "tokens_lite",
        "roundtrip_core_fidelity",
    }


def test_summarize_empty_trace_has_zero_ratios() -> None:
    summary = bench.summarize([], None, "x")
    assert summary["messages"] == 0
    assert summary["bytes"]["lite_vs_raw_wire_ratio"] == 0.0
    assert summary["tokens"]["lite_vs_raw_wire_ratio"] == 0.0
    assert summary["roundtrip_core_fidelity"] is True
    assert summary["by_type"] == {}


def test_load_trace_reads_committed_sample() -> None:
    trace = bench.load_trace(bench.DEFAULT_TRACE)
    assert len(trace) == 12
    assert trace[0]["type"] == "presence_update"


def test_run_writes_results_and_returns_summary(tmp_path: Path) -> None:
    results = tmp_path / "out" / "result.json"
    summary = bench.run(bench.DEFAULT_TRACE, results)
    assert summary["trace"] == "sample_session.json"
    assert summary["messages"] == 12
    assert summary["roundtrip_core_fidelity"] is True
    written = json.loads(results.read_text(encoding="utf-8"))
    assert written["messages"] == 12


def test_run_without_results_path_skips_write(tmp_path: Path) -> None:
    summary = bench.run(bench.DEFAULT_TRACE, None)
    assert summary["messages"] == 12
    assert not list(tmp_path.iterdir())


def test_main_runs_and_writes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "result.json"
    assert bench.main(["--results", str(results)]) == 0
    out = capsys.readouterr().out
    assert "core-field roundtrip fidelity: True" in out
    assert results.exists()
