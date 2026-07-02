# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — scorecard baseline comparison regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.benchmark.comparison import (
    DEFAULT_TOLERANCE_PCT,
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    compare_scorecards,
    comparison_to_json,
    load_baseline,
    metric_direction,
    render_comparison_human,
)
from synapse_channel.benchmark.probes import ProbeResult
from synapse_channel.benchmark.scorecard import (
    NON_ISOLATED_LABEL,
    HostContext,
    Scorecard,
    scorecard_to_json,
)


def _context(**overrides: object) -> HostContext:
    fields: dict[str, object] = {
        "package_version": "0.90.0",
        "python": "3.12.3",
        "platform": "Linux-test",
        "cpu_model": "Test CPU 9000",
        "cpu_count": 8,
        "governor": "performance",
        "load_before": (0.5, 0.4, 0.3),
        "load_after": (0.6, 0.5, 0.4),
        "isolation": NON_ISOLATED_LABEL,
        "started_at": 1000.0,
    }
    fields.update(overrides)
    return HostContext(**fields)  # type: ignore[arg-type]


def _result(name: str, metrics: dict[str, float]) -> ProbeResult:
    return ProbeResult(name=name, iterations=10, duration_seconds=0.1, metrics=metrics)


def _scorecard(*results: ProbeResult, **context_overrides: object) -> Scorecard:
    return Scorecard(context=_context(**context_overrides), results=tuple(results))


def _baseline_document(*results: ProbeResult, **context_overrides: object) -> dict[str, object]:
    return dict(scorecard_to_json(_scorecard(*results, **context_overrides)))


# --- direction classification -------------------------------------------------


def test_metric_direction_classifies_gated_and_ungated_names() -> None:
    assert metric_direction("events_per_second") == HIGHER_IS_BETTER
    assert metric_direction("p95_ms") == LOWER_IS_BETTER
    assert metric_direction("lite_to_raw_ratio") == ""
    assert metric_direction("live_claims_rebuilt") == ""


# --- comparison verdicts --------------------------------------------------------


def test_throughput_drop_beyond_tolerance_is_a_regression() -> None:
    baseline = _baseline_document(_result("append", {"events_per_second": 1000.0}))
    current = _scorecard(_result("append", {"events_per_second": 700.0}))
    comparison = compare_scorecards(baseline, current, tolerance_pct=25.0)
    (delta,) = comparison.deltas
    assert delta.regression
    assert delta.change_pct == pytest.approx(-30.0)
    assert comparison.regressions == (delta,)


def test_latency_rise_beyond_tolerance_is_a_regression() -> None:
    baseline = _baseline_document(_result("grant", {"p95_ms": 2.0}))
    current = _scorecard(_result("grant", {"p95_ms": 3.0}))
    comparison = compare_scorecards(baseline, current, tolerance_pct=25.0)
    (delta,) = comparison.deltas
    assert delta.regression
    assert delta.change_pct == pytest.approx(50.0)


def test_drift_within_tolerance_and_improvements_pass() -> None:
    baseline = _baseline_document(_result("append", {"events_per_second": 1000.0, "p50_ms": 2.0}))
    current = _scorecard(_result("append", {"events_per_second": 900.0, "p50_ms": 1.0}))
    comparison = compare_scorecards(baseline, current, tolerance_pct=25.0)
    assert [delta.regression for delta in comparison.deltas] == [False, False]
    assert comparison.regressions == ()


def test_ungated_metrics_and_zero_baselines_are_skipped() -> None:
    baseline = _baseline_document(
        _result("encode", {"lite_to_raw_ratio": 0.4, "messages_per_second": 0.0})
    )
    current = _scorecard(
        _result("encode", {"lite_to_raw_ratio": 0.9, "messages_per_second": 100.0})
    )
    comparison = compare_scorecards(baseline, current)
    assert comparison.deltas == ()
    assert comparison.tolerance_pct == DEFAULT_TOLERANCE_PCT


def test_probe_sets_are_reconciled_not_fatal() -> None:
    baseline = _baseline_document(
        _result("append", {"events_per_second": 1000.0}),
        _result("replay", {"events_per_second": 5000.0}),
    )
    current = _scorecard(
        _result("append", {"events_per_second": 1100.0}),
        _result("grant", {"p95_ms": 1.0}),
    )
    comparison = compare_scorecards(baseline, current)
    assert comparison.missing_probes == ("replay",)
    assert comparison.new_probes == ("grant",)
    assert [delta.probe for delta in comparison.deltas] == ["append"]


def test_metric_missing_from_baseline_probe_is_skipped() -> None:
    baseline = _baseline_document(_result("append", {"events_per_second": 1000.0}))
    current = _scorecard(_result("append", {"events_per_second": 990.0, "p95_ms": 1.0}))
    comparison = compare_scorecards(baseline, current)
    assert [delta.metric for delta in comparison.deltas] == ["events_per_second"]


def test_malformed_baseline_probe_metrics_are_ignored() -> None:
    baseline = _baseline_document(_result("append", {"events_per_second": 1000.0}))
    results = baseline["results"]
    assert isinstance(results, list)
    results.append({"name": "odd", "metrics": "not-a-table"})
    results[0]["metrics"]["stray"] = "text"
    current = _scorecard(_result("append", {"events_per_second": 1000.0}))
    comparison = compare_scorecards(baseline, current)
    assert [delta.metric for delta in comparison.deltas] == ["events_per_second"]


# --- host-context guard ---------------------------------------------------------


def test_different_cpu_model_is_refused() -> None:
    baseline = _baseline_document(
        _result("append", {"events_per_second": 1000.0}), cpu_model="Other CPU 1"
    )
    current = _scorecard(_result("append", {"events_per_second": 1000.0}))
    with pytest.raises(ValueError, match="baseline host does not match"):
        compare_scorecards(baseline, current)


def test_soft_context_drift_becomes_loud_warnings() -> None:
    baseline = _baseline_document(
        _result("append", {"events_per_second": 1000.0}),
        governor="powersave",
        package_version="0.89.0",
    )
    current = _scorecard(_result("append", {"events_per_second": 1000.0}))
    comparison = compare_scorecards(baseline, current)
    assert len(comparison.context_warnings) == 2
    assert any("governor differs" in warning for warning in comparison.context_warnings)
    assert any("package_version differs" in warning for warning in comparison.context_warnings)


# --- baseline loading -----------------------------------------------------------


def test_load_baseline_round_trips_a_results_file(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    document = _baseline_document(_result("append", {"events_per_second": 1000.0}))
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = load_baseline(path)
    assert loaded["context"]["cpu_model"] == "Test CPU 9000"


def test_load_baseline_refuses_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read baseline"):
        load_baseline(tmp_path / "absent.json")


def test_load_baseline_refuses_non_json(tmp_path: Path) -> None:
    path = tmp_path / "junk.json"
    path.write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline is not JSON"):
        load_baseline(path)


@pytest.mark.parametrize(
    "document",
    [
        [1, 2],  # not an object
        {"results": []},  # no context
        {"context": {"cpu_model": 7}, "results": []},  # cpu_model not a string
        {"context": {"cpu_model": "x"}},  # no results
        {"context": {"cpu_model": "x"}, "results": [{"metrics": {}}]},  # nameless entry
    ],
)
def test_load_baseline_refuses_non_scorecard_shapes(tmp_path: Path, document: object) -> None:
    path = tmp_path / "shape.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="baseline is not a scorecard"):
        load_baseline(path)


# --- rendering ------------------------------------------------------------------


def test_human_rendering_names_verdicts_warnings_and_probe_sets() -> None:
    baseline = _baseline_document(
        _result("append", {"events_per_second": 1000.0, "p95_ms": 1.0}),
        _result("replay", {"events_per_second": 5000.0}),
        governor="powersave",
    )
    current = _scorecard(
        _result("append", {"events_per_second": 500.0, "p95_ms": 1.1}),
        _result("grant", {"p95_ms": 1.0}),
    )
    comparison = compare_scorecards(baseline, current, tolerance_pct=25.0)
    text = render_comparison_human(comparison)
    assert "Baseline comparison (tolerance ±25%)" in text
    assert "WARNING: governor differs" in text
    assert (
        "append/events_per_second: 1,000.00 -> 500.00 (-50.0%, higher-is-better) REGRESSION"
    ) in text
    assert "append/p95_ms: 1.00 -> 1.10 (+10.0%, lower-is-better) ok" in text
    assert "not run this time: replay" in text
    assert "not in the baseline: grant" in text
    assert "1 regression beyond ±25% tolerance" in text


def test_human_rendering_with_nothing_shared_is_honest() -> None:
    baseline = _baseline_document(_result("replay", {"events_per_second": 5000.0}))
    current = _scorecard(_result("grant", {"p95_ms": 1.0}))
    comparison = compare_scorecards(baseline, current)
    text = render_comparison_human(comparison)
    assert "no gated metrics shared with the baseline" in text
    assert "0 regressions beyond" in text


def test_human_rendering_of_matching_probe_sets_omits_the_set_notes() -> None:
    baseline = _baseline_document(_result("append", {"events_per_second": 1000.0}))
    current = _scorecard(_result("append", {"events_per_second": 1000.0}))
    text = render_comparison_human(compare_scorecards(baseline, current))
    assert "not run this time" not in text
    assert "not in the baseline" not in text
    assert "0 regressions beyond" in text


def test_comparison_json_carries_the_full_shape() -> None:
    baseline = _baseline_document(_result("append", {"events_per_second": 1000.0}))
    current = _scorecard(_result("append", {"events_per_second": 100.0}))
    comparison = compare_scorecards(baseline, current, tolerance_pct=10.0)
    payload = comparison_to_json(comparison)
    assert payload["regressed"] is True
    assert payload["tolerance_pct"] == 10.0
    deltas = payload["deltas"]
    assert isinstance(deltas, list)
    (delta,) = deltas
    assert delta == {
        "probe": "append",
        "metric": "events_per_second",
        "baseline": 1000.0,
        "current": 100.0,
        "change_pct": -90.0,
        "direction": HIGHER_IS_BETTER,
        "regression": True,
    }
