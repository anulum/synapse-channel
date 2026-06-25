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

_BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
_BENCH_PATH = _BENCHMARKS / "relay_token_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("relay_token_benchmark", _BENCH_PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)

_ROUTING_PATH = _BENCHMARKS / "routing_benchmark.py"
_ROUTING_SPEC = importlib.util.spec_from_file_location("routing_benchmark", _ROUTING_PATH)
assert _ROUTING_SPEC is not None and _ROUTING_SPEC.loader is not None
routing_bench = importlib.util.module_from_spec(_ROUTING_SPEC)
_ROUTING_SPEC.loader.exec_module(routing_bench)

_A2A_PATH = _BENCHMARKS / "a2a_bridge_benchmark.py"
_A2A_SPEC = importlib.util.spec_from_file_location("a2a_bridge_benchmark", _A2A_PATH)
assert _A2A_SPEC is not None and _A2A_SPEC.loader is not None
a2a_bench = importlib.util.module_from_spec(_A2A_SPEC)
_A2A_SPEC.loader.exec_module(a2a_bench)


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
    class WhitespaceTokenEncoder:
        def encode(self, text: str) -> list[int]:
            return list(range(len(text.split())))

    assert bench.count_tokens("a b c", WhitespaceTokenEncoder()) == 3


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


# --- routing benchmark -------------------------------------------------------


def test_routing_tag_backend_returns_its_tag() -> None:
    backend = routing_bench._TagBackend("rule")
    assert backend.generate(system_prompt="", user_prompt="x") == "rule"


def test_routing_load_prompts_reads_committed_set() -> None:
    prompts = routing_bench.load_prompts(routing_bench.DEFAULT_TRACE)
    assert len(prompts) == 15
    assert prompts[0] == "hi"


def test_routing_summarize_distribution_and_dispatch() -> None:
    summary = routing_bench.summarize(["hi", "design a coordination system for agents now"])
    assert summary["prompts"] == 2
    assert summary["distribution"]["rule"] == 1
    assert summary["distribution"]["heavy"] == 1
    assert summary["routing_verified"] is True


def test_routing_run_writes_results(tmp_path: Path) -> None:
    results = tmp_path / "routing.json"
    summary = routing_bench.run(routing_bench.DEFAULT_TRACE, results)
    assert summary["trace"] == "routing_prompts.json"
    assert summary["prompts"] == 15
    written = json.loads(results.read_text(encoding="utf-8"))
    assert written["routing_verified"] is True


def test_routing_run_without_results_skips_write(tmp_path: Path) -> None:
    summary = routing_bench.run(routing_bench.DEFAULT_TRACE, None)
    assert summary["prompts"] == 15
    assert not list(tmp_path.iterdir())


def test_routing_main_runs_and_writes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "routing.json"
    assert routing_bench.main(["--results", str(results)]) == 0
    assert "routing dispatch verified: True" in capsys.readouterr().out
    assert results.exists()


# --- scalability benchmark ---------------------------------------------------

_SCALE_PATH = _BENCHMARKS / "scalability_benchmark.py"
_SCALE_SPEC = importlib.util.spec_from_file_location("scalability_benchmark", _SCALE_PATH)
assert _SCALE_SPEC is not None and _SCALE_SPEC.loader is not None
scale_bench = importlib.util.module_from_spec(_SCALE_SPEC)
_SCALE_SPEC.loader.exec_module(scale_bench)


def test_scalability_host_profile_has_fields() -> None:
    host = scale_bench.host_profile()
    assert set(host) == {"cpu", "python", "platform"}
    assert host["python"]


def test_scalability_state_with_claims_builds_count() -> None:
    state = scale_bench.state_with_claims(5)
    assert len(state.claims) == 5
    assert len(state._lease_heap) == 5  # the heap is populated to match


def test_scalability_mass_expiry_drains_every_claim() -> None:
    assert scale_bench.measure_mass_expiry_seconds(50) >= 0.0


def test_scalability_state_with_scoped_claims_builds_distinct_paths() -> None:
    state = scale_bench.state_with_scoped_claims(4)
    assert len(state.claims) == 4
    assert state.claims["T0"].paths == ("d0/file",)
    assert len(state._lease_heap) == 4


def test_scalability_measure_claim_scan_is_nonnegative() -> None:
    assert scale_bench.measure_claim_scan_seconds(20, iterations=3) >= 0.0


def test_scalability_profile_reports_expiry_replay_and_scan() -> None:
    rows = scale_bench.profile(
        claim_counts=(10, 100), replay_counts=(10,), iterations=3, scan_iterations=3
    )
    assert [row["active_claims"] for row in rows["expiry"]] == [10, 100]
    assert all(row["steady_heartbeat_microseconds"] >= 0 for row in rows["expiry"])
    assert all(row["mass_expiry_microseconds"] >= 0 for row in rows["expiry"])
    assert rows["replay"][0]["events"] == 10
    assert rows["replay"][0]["replay_milliseconds"] >= 0
    assert rows["replay"][0]["events_per_sec"] >= 0
    assert [row["active_claims"] for row in rows["scan"]] == [10, 100]
    assert all(row["claim_scan_microseconds"] >= 0 for row in rows["scan"])


def test_scalability_run_writes_results(tmp_path: Path) -> None:
    results = tmp_path / "scale.json"
    summary = scale_bench.run(
        results, iterations=3, claim_counts=(10, 100), replay_counts=(10,), scan_iterations=3
    )
    assert set(summary["host"]) == {"cpu", "python", "platform"}
    assert len(summary["expiry"]) == 2
    assert len(summary["scan"]) == 2
    written = json.loads(results.read_text(encoding="utf-8"))
    assert len(written["expiry"]) == 2
    assert len(written["replay"]) == 1
    assert len(written["scan"]) == 2


def test_scalability_run_without_results_skips_write(tmp_path: Path) -> None:
    summary = scale_bench.run(None, iterations=3, claim_counts=(10,), replay_counts=(10,))
    assert summary["expiry"]
    assert not list(tmp_path.iterdir())


def test_scalability_counts_parses_overrides() -> None:
    assert scale_bench._counts("10,100", (1,)) == (10, 100)
    assert scale_bench._counts(None, (1, 2)) == (1, 2)


def test_scalability_main_runs_and_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = tmp_path / "scale.json"
    rc = scale_bench.main(
        [
            "--results",
            str(results),
            "--iterations",
            "3",
            "--scan-iterations",
            "3",
            "--claim-counts",
            "10,100",
            "--replay-counts",
            "10",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "event replay" in out
    assert "scope-conflict scan" in out
    assert results.exists()


# --- A2A bridge benchmark ----------------------------------------------------


def test_a2a_benchmark_profile_reports_operations() -> None:
    summary = a2a_bench.profile(task_count=8, subscriber_count=3)

    assert summary["tasks"] == 8
    assert summary["subscriber_count"] == 3
    assert summary["task_creation"]["tasks_per_sec"] > 0
    assert summary["correlation"]["tasks_per_sec"] > 0
    assert summary["listing"]["tasks"] == 8
    assert summary["push_delivery"]["deliveries"] == 8
    assert summary["subscriber_fanout"]["events"] == 3
    assert summary["correlated_replies"] == 8


def test_a2a_benchmark_run_writes_results(tmp_path: Path) -> None:
    results = tmp_path / "a2a.json"

    summary = a2a_bench.run(results, task_count=5, subscriber_count=2)

    assert summary["tasks"] == 5
    written = json.loads(results.read_text(encoding="utf-8"))
    assert written["tasks"] == 5
    assert written["subscriber_fanout"]["events"] == 2


def test_a2a_benchmark_main_runs_and_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = tmp_path / "a2a.json"

    rc = a2a_bench.main(["--results", str(results), "--task-count", "5", "--subscriber-count", "2"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "A2A bridge benchmark" in out
    assert "subscriber fanout events: 2" in out
    assert results.exists()
