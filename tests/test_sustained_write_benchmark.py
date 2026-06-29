# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the sustained-write event-store benchmark harness

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_BENCH_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "sustained_write_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("sustained_write_benchmark", _BENCH_PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)

_LATENCY_KEYS = {"mean_us", "p50_us", "p95_us", "p99_us", "max_us"}


def test_percentiles_are_ordered_and_in_microseconds() -> None:
    stats = bench._percentiles([0.001, 0.002, 0.003, 0.004])  # seconds
    assert stats["p50_us"] <= stats["p95_us"] <= stats["p99_us"] <= stats["max_us"]
    assert stats["max_us"] == 4000.0  # 0.004 s -> 4000 us
    assert _LATENCY_KEYS <= set(stats)


def test_host_profile_records_attribution() -> None:
    profile = bench.host_profile()
    assert set(profile) == {"cpu", "python", "platform"}
    assert profile["python"]


def test_write_latency_reports_distribution_and_throughput() -> None:
    for durable in (False, True):
        row = bench.measure_write_latency(40, durable=durable)
        assert row["count"] == 40
        assert row["durable"] is durable
        assert row["throughput_eps"] > 0
        assert _LATENCY_KEYS <= set(row)
        assert all(row[key] >= 0 for key in _LATENCY_KEYS)


def test_read_since_grows_with_the_log() -> None:
    small = bench.measure_read_since_seconds(50)
    large = bench.measure_read_since_seconds(500)
    assert small > 0 and large > 0
    assert large > small  # an O(events) replay scales with the retained log


def test_compaction_removes_the_oldest_half() -> None:
    impact = bench.measure_compaction_read_impact(80)
    assert impact["count"] == 80
    assert impact["removed"] == 40  # the oldest half
    assert impact["before_seconds"] >= 0 and impact["after_seconds"] >= 0


def test_collect_returns_every_section() -> None:
    result = bench.collect(
        sustained_count=30, durable_count=10, read_counts=(20, 40), compaction_count=30
    )
    assert {"write_latency", "read_since", "compaction_read_impact"} == set(result)
    assert len(result["write_latency"]) == 2
    assert [row["count"] for row in result["read_since"]] == [20, 40]


def test_run_attaches_host_and_optionally_writes(tmp_path: Path) -> None:
    out = tmp_path / "sub" / "result.json"
    summary = bench.run(
        out, sustained_count=20, durable_count=8, read_counts=(10,), compaction_count=20
    )
    assert "host" in summary and "write_latency" in summary
    assert json.loads(out.read_text(encoding="utf-8"))["host"]["python"]

    # write=False leaves no file behind
    no_file = tmp_path / "absent.json"
    bench.run(
        no_file,
        write=False,
        sustained_count=10,
        durable_count=5,
        read_counts=(5,),
        compaction_count=10,
    )
    assert not no_file.exists()


def test_main_runs_and_writes_results(tmp_path: Path) -> None:
    out = tmp_path / "main.json"
    # tiny counts keep the test fast; the harness's defaults run the full profile
    code = bench.main(
        [
            "--results",
            str(out),
            "--sustained-count",
            "20",
            "--durable-count",
            "6",
            "--read-counts",
            "10",
            "20",
            "--compaction-count",
            "20",
        ]
    )
    assert code == 0
    assert out.is_file()
    assert json.loads(out.read_text(encoding="utf-8"))["write_latency"]
