# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — benchmark CLI command regressions

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from synapse_channel.benchmark.probes import PROBES, ProbeResult
from synapse_channel.benchmark.scorecard import (
    NON_ISOLATED_LABEL,
    Scorecard,
    capture_host_context,
)
from synapse_channel.benchmark.trend import SPARK_LEVELS, append_scorecard
from synapse_channel.cli import build_parser, main


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_list_names_every_probe(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["benchmark", "--list"], capsys)
    assert code == 0
    assert err == ""
    for name in PROBES:
        assert name in out
    assert "default" in out


def test_single_probe_human_scorecard(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["benchmark", "--probe", "encode-lite", "--iterations", "10"], capsys)
    assert code == 0
    assert "benchmark scorecard" in out
    assert f"isolation: {NON_ISOLATED_LABEL}" in out
    assert "encode-lite: 10 iterations" in out


def test_json_scorecard_parses_and_carries_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = _run(
        ["benchmark", "--probe", "encode-lite", "--iterations", "10", "--json"], capsys
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["context"]["isolation"] == NON_ISOLATED_LABEL
    assert payload["results"][0]["name"] == "encode-lite"
    assert payload["results"][0]["iterations"] == 10


def test_results_file_is_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "out" / "scorecard.json"
    code, _, _ = _run(
        [
            "benchmark",
            "--probe",
            "encode-lite",
            "--iterations",
            "10",
            "--results",
            str(target),
        ],
        capsys,
    )
    assert code == 0
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["results"][0]["metrics"]["lite_to_raw_ratio"] < 1


def test_unknown_probe_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["benchmark", "--probe", "nonesuch"], capsys)
    assert code == 2
    assert out == ""
    assert "unknown probe" in err


def test_non_positive_iterations_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(["benchmark", "--probe", "encode-lite", "--iterations", "0"], capsys)
    assert code == 2
    assert "iterations must be positive" in err


def test_live_hub_probe_through_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    # The real-socket boundary through the full command path: a hub is
    # started, an agent connects, and round-trips are measured.
    code, out, _ = _run(
        ["benchmark", "--probe", "hub-roundtrip", "--iterations", "3", "--json"], capsys
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["results"][0]["metrics"]["roundtrips_per_second"] > 0


def test_parser_flags_and_defaults() -> None:
    parser = build_parser(command="benchmark")
    args = parser.parse_args(["benchmark"])
    assert args.probe is None
    assert args.iterations is None
    assert args.results is None
    assert args.list is False
    assert args.json is False
    assert args.compare is None
    assert args.tolerance is None
    assert args.trend is None
    assert args.ascii is False
    assert args.alert is False
    assert args.alert_sigma is None
    assert args.alert_min_samples is None


def test_trend_accumulates_runs_and_renders_the_series(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "trend.db"
    argv = ["benchmark", "--probe", "encode-lite", "--iterations", "10", "--trend", str(db)]

    first_code, first_out, _ = _run(argv, capsys)
    second_code, second_out, _ = _run(argv, capsys)

    assert (first_code, second_code) == (0, 0)
    assert "Benchmark trend: 1 stored run(s)" in first_out
    assert "(1 run — no trend yet)" in first_out
    assert "Benchmark trend: 2 stored run(s)" in second_out
    assert "encode-lite messages_per_second: " in second_out
    assert ", 2 runs)" in second_out


def test_ascii_without_trend_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(["benchmark", "--probe", "encode-lite", "--ascii"], capsys)
    assert code == 2
    assert "--ascii requires --trend" in err


def test_trend_ascii_renders_a_pure_ascii_trend_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "trend.db"
    argv = [
        "benchmark",
        "--probe",
        "encode-lite",
        "--iterations",
        "10",
        "--trend",
        str(db),
        "--ascii",
    ]

    first_code, first_out, _ = _run(argv, capsys)
    second_code, second_out, _ = _run(argv, capsys)

    assert (first_code, second_code) == (0, 0)
    assert "(1 run -- no trend yet)" in first_out
    assert "Benchmark trend: 2 stored run(s)" in second_out
    trend_block = second_out.split("Benchmark trend:", 1)[1]
    assert trend_block.isascii()
    assert not any(glyph in trend_block for glyph in SPARK_LEVELS)


def _seed_same_context_history(db: Path, values: list[float]) -> None:
    """Store synthetic encode-lite runs carrying THIS host's real context.

    The real benchmark run that follows appends to the same context segment,
    so the drift gate sees one comparable population — the only setup that
    makes an end-to-end alert deterministic.
    """
    context = capture_host_context()
    for index, value in enumerate(values):
        result = ProbeResult(
            name="encode-lite",
            iterations=10,
            duration_seconds=0.1,
            metrics={"messages_per_second": value},
        )
        append_scorecard(
            db,
            Scorecard(
                context=dataclasses.replace(context, started_at=float(index)),
                results=(result,),
            ),
        )


def test_alert_without_trend_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(["benchmark", "--probe", "encode-lite", "--alert"], capsys)
    assert code == 2
    assert "--alert requires --trend" in err


def test_alert_tuning_flags_require_alert(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for flag in (["--alert-sigma", "2"], ["--alert-min-samples", "4"]):
        code, _, err = _run(
            ["benchmark", "--probe", "encode-lite", "--trend", str(tmp_path / "t.db"), *flag],
            capsys,
        )
        assert code == 2
        assert "--alert-sigma/--alert-min-samples require --alert" in err


def test_alert_rejects_invalid_thresholds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["benchmark", "--probe", "encode-lite", "--trend", str(tmp_path / "t.db"), "--alert"]

    code, _, err = _run([*base, "--alert-sigma", "0"], capsys)
    assert code == 2
    assert "--alert-sigma must be positive" in err

    code, _, err = _run([*base, "--alert-min-samples", "2"], capsys)
    assert code == 2
    assert "--alert-min-samples must be at least 3" in err


def test_alert_flags_a_flat_baseline_deviation_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "trend.db"
    _seed_same_context_history(db, [100.0, 100.0, 100.0, 100.0])

    code, out, _ = _run(
        [
            "benchmark",
            "--probe",
            "encode-lite",
            "--iterations",
            "10",
            "--trend",
            str(db),
            "--alert",
        ],
        capsys,
    )

    # a real run measures far above the planted 100 msg/s flat baseline
    assert code == 1
    assert "DRIFT encode-lite messages_per_second:" in out
    assert "off a flat baseline" in out
    assert "insufficient samples" in out  # the real run's other metrics have one sample


def test_alert_with_insufficient_history_reports_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "trend.db"
    argv = [
        "benchmark",
        "--probe",
        "encode-lite",
        "--iterations",
        "10",
        "--trend",
        str(db),
        "--alert",
    ]

    first_code, first_out, _ = _run(argv, capsys)
    second_code, second_out, _ = _run(argv, capsys)

    assert (first_code, second_code) == (0, 0)
    for out in (first_out, second_out):
        assert "Drift alert: 0 finding(s)" in out
        assert "insufficient samples" in out
        assert "not gated" in out


def test_alert_json_carries_the_drift_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "trend.db"
    _seed_same_context_history(db, [100.0, 100.0, 100.0, 100.0])

    code, out, _ = _run(
        [
            "benchmark",
            "--probe",
            "encode-lite",
            "--iterations",
            "10",
            "--trend",
            str(db),
            "--alert",
            "--json",
        ],
        capsys,
    )

    assert code == 1
    payload = json.loads(out)
    drift = payload["drift"]
    assert drift["findings"][0]["metric"] == "messages_per_second"
    assert drift["note"] == "same-context statistics only; an insufficient series is never gated"


def test_trend_json_carries_the_history(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "trend.db"

    code, out, _ = _run(
        ["benchmark", "--probe", "encode-lite", "--iterations", "10", "--trend", str(db), "--json"],
        capsys,
    )

    assert code == 0
    payload = json.loads(out)
    assert len(payload["trend"]["runs"]) == 1
    assert payload["trend"]["context_breaks"] == []


def test_trend_unwritable_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied", encoding="utf-8")

    code, _, err = _run(
        [
            "benchmark",
            "--probe",
            "encode-lite",
            "--iterations",
            "10",
            "--trend",
            str(blocker / "trend.db"),
        ],
        capsys,
    )

    assert code == 2
    assert "cannot record the trend run" in err


def _write_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, messages_per_second: float
) -> Path:
    """Record a real encode-lite baseline, then pin its throughput value."""
    path = tmp_path / "baseline.json"
    code, _, _ = _run(
        ["benchmark", "--probe", "encode-lite", "--iterations", "10", "--results", str(path)],
        capsys,
    )
    assert code == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    document["results"][0]["metrics"]["messages_per_second"] = messages_per_second
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_compare_against_a_slower_baseline_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A real current run is always faster than a 0.001 msg/s baseline.
    baseline = _write_baseline(tmp_path, capsys, messages_per_second=0.001)
    code, out, _ = _run(
        ["benchmark", "--probe", "encode-lite", "--iterations", "10", "--compare", str(baseline)],
        capsys,
    )
    assert code == 0
    assert "Baseline comparison" in out
    assert "0 regressions beyond" in out


def test_compare_against_an_impossible_baseline_regresses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No real run reaches 1e12 msg/s: the drop always exceeds the tolerance.
    baseline = _write_baseline(tmp_path, capsys, messages_per_second=1e12)
    code, out, _ = _run(
        ["benchmark", "--probe", "encode-lite", "--iterations", "10", "--compare", str(baseline)],
        capsys,
    )
    assert code == 1
    assert "REGRESSION" in out


def test_compare_json_document_carries_the_comparison(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_baseline(tmp_path, capsys, messages_per_second=1e12)
    code, out, _ = _run(
        [
            "benchmark",
            "--probe",
            "encode-lite",
            "--iterations",
            "10",
            "--compare",
            str(baseline),
            "--json",
        ],
        capsys,
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["comparison"]["regressed"] is True
    (delta,) = [
        entry
        for entry in payload["comparison"]["deltas"]
        if entry["metric"] == "messages_per_second"
    ]
    assert delta["regression"] is True


def test_compare_refuses_a_baseline_from_another_host(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_baseline(tmp_path, capsys, messages_per_second=1.0)
    document = json.loads(baseline.read_text(encoding="utf-8"))
    document["context"]["cpu_model"] = "Entirely Different CPU"
    baseline.write_text(json.dumps(document), encoding="utf-8")
    code, _, err = _run(
        ["benchmark", "--probe", "encode-lite", "--iterations", "10", "--compare", str(baseline)],
        capsys,
    )
    assert code == 2
    assert "baseline host does not match" in err


def test_compare_refuses_a_malformed_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "junk.json"
    path.write_text("{nope", encoding="utf-8")
    code, _, err = _run(
        ["benchmark", "--probe", "encode-lite", "--iterations", "10", "--compare", str(path)],
        capsys,
    )
    assert code == 2
    assert "baseline is not JSON" in err


def test_tolerance_without_compare_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(["benchmark", "--tolerance", "10"], capsys)
    assert code == 2
    assert "--tolerance requires --compare" in err


def test_non_positive_tolerance_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(
        ["benchmark", "--compare", str(tmp_path / "b.json"), "--tolerance", "0"], capsys
    )
    assert code == 2
    assert "tolerance must be positive" in err


def test_export_csv_requires_trend(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(["benchmark", "--probe", "encode-lite", "--export-csv", "out.csv"], capsys)
    assert code == 2
    assert "--export-csv requires --trend" in err


def test_export_csv_writes_the_history(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "trend.db"
    out = tmp_path / "trend.csv"

    code, stdout, _ = _run(
        [
            "benchmark",
            "--probe",
            "encode-lite",
            "--iterations",
            "10",
            "--trend",
            str(db),
            "--export-csv",
            str(out),
        ],
        capsys,
    )

    assert code == 0
    assert f"trend CSV written to {out}" in stdout
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("run_id,started_at,package_version")
    assert any("encode-lite,messages_per_second," in line for line in lines[1:])


def test_export_csv_fails_visible_on_an_unwritable_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied", encoding="utf-8")

    code, _, err = _run(
        [
            "benchmark",
            "--probe",
            "encode-lite",
            "--iterations",
            "10",
            "--trend",
            str(tmp_path / "trend.db"),
            "--export-csv",
            str(blocker / "trend.csv"),
        ],
        capsys,
    )

    assert code == 2
    assert "cannot write the trend CSV" in err
