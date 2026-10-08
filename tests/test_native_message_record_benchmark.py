# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the native-message record benchmark measures what it says
"""Run the benchmark's measurements at small scale and check the committed result."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_BENCH_PATH = (
    Path(__file__).resolve().parents[1] / "benchmarks" / "native_message_record_benchmark.py"
)
_SPEC = importlib.util.spec_from_file_location("native_message_record_benchmark", _BENCH_PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)

_LATENCY_KEYS = {"mean_us", "p50_us", "p95_us", "p99_us", "max_us"}


def test_percentiles_are_ordered_and_in_microseconds() -> None:
    stats = bench._percentiles([0.001, 0.002, 0.003, 0.004])
    assert stats["p50_us"] <= stats["p95_us"] <= stats["p99_us"] <= stats["max_us"]
    assert stats["max_us"] == 4000.0
    assert set(stats) == _LATENCY_KEYS


def test_host_and_load_are_recorded() -> None:
    assert set(bench.host_profile()) == {"cpu", "python", "platform"}
    assert len(bench.load_average()) in {0, 3}


def test_frames_are_valid_distinct_records_of_the_requested_size() -> None:
    first, second = bench.record_frame(0, 300), bench.record_frame(1, 300)
    assert first["text_bytes"] == second["text_bytes"] == 300
    assert first["idem_key"] != second["idem_key"]
    assert first["native_message_id"] != second["native_message_id"]


def test_parse_measurement_reports_distribution_and_throughput() -> None:
    row = bench.measure_parse(50, 128)
    assert (row["count"], row["text_bytes"]) == (50, 128)
    assert row["throughput_per_second"] > 0
    assert _LATENCY_KEYS <= set(row)


def test_round_trip_measurement_stores_every_record_in_a_real_journal() -> None:
    row = bench.measure_round_trip(12, 128)
    assert (row["count"], row["stored_events"], row["text_bytes"]) == (12, 12, 128)
    assert row["throughput_per_second"] > 0
    assert row["p50_us"] <= row["max_us"]


def test_round_trip_above_the_event_quota_raises_instead_of_reporting_a_partial_run() -> None:
    with pytest.raises(RuntimeError, match="native_record_rate_limited"):
        bench.measure_round_trip(bench.QUOTA_EVENTS + 1, 16)


def test_documented_runs_stay_inside_the_event_and_byte_quota() -> None:
    for text_bytes, count in bench.ROUND_TRIP_RUNS:
        frame_bytes = len(json.dumps(bench.record_frame(count, text_bytes)))
        assert count <= bench.QUOTA_EVENTS
        assert frame_bytes * count < bench.QUOTA_BYTES


def test_run_attaches_context_and_optionally_writes(tmp_path: Path) -> None:
    out = tmp_path / "sub" / "result.json"
    summary = bench.run(out, parse_count=20, text_sizes=(64,), round_trip_runs=((64, 5),))
    assert json.loads(out.read_text(encoding="utf-8")) == summary
    assert summary["isolation"].startswith("none")
    assert summary["ingress_quota"] == {
        "events": 600,
        "bytes": 8_388_608,
        "window_seconds": 60.0,
    }
    assert [(row["text_bytes"], row["count"]) for row in summary["round_trip"]] == [(64, 5)]
    silent = bench.run(
        tmp_path / "absent.json", write=False, parse_count=5, round_trip_runs=((16, 2),)
    )
    assert not (tmp_path / "absent.json").exists()
    assert len(silent["parse"]) == len(bench.TEXT_SIZES)


def test_main_writes_the_result_and_prints_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "result.json"
    argv = ["--results", str(out), "--parse-count", "10", "--text-sizes", "32"]
    code = bench.main([*argv, "--round-trip", "32", "3"])
    assert code == 0
    assert "durable record round trip: p50 " in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["round_trip"][0]["stored_events"] == 3


def test_committed_result_matches_the_documented_runs() -> None:
    committed = json.loads(bench.DEFAULT_RESULTS.read_text(encoding="utf-8"))
    assert committed["isolation"].startswith("none")
    assert len(committed["load_average_before"]) == 3
    runs = [(row["text_bytes"], row["count"]) for row in committed["round_trip"]]
    assert runs == list(bench.ROUND_TRIP_RUNS)
    assert all(row["stored_events"] == row["count"] for row in committed["round_trip"])
    assert [row["text_bytes"] for row in committed["parse"]] == list(bench.TEXT_SIZES)
