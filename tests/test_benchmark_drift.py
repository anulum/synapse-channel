# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — benchmark trend drift-gate regressions

from __future__ import annotations

import statistics

import pytest

from synapse_channel.benchmark.drift import (
    DEFAULT_ALERT_SIGMA,
    DEFAULT_MIN_SAMPLES,
    MIN_SAMPLES_FLOOR,
    assess_drift,
    drift_to_json,
    render_drift_human,
    same_context_segment,
)
from synapse_channel.benchmark.trend import StoredRun


def _run(
    run_id: int,
    events_per_second: float,
    *,
    package: str = "0.92.0",
    cpu: str = "Test CPU 9000",
    governor: str = "performance",
    extra_metrics: dict[str, float] | None = None,
) -> StoredRun:
    metrics = {"event-store-append": {"events_per_second": events_per_second}}
    if extra_metrics:
        metrics["event-store-append"].update(extra_metrics)
    return StoredRun(
        run_id=run_id,
        started_at=float(run_id) * 1000.0,
        package_version=package,
        cpu_model=cpu,
        governor=governor,
        metrics=metrics,
    )


def _history(*values: float) -> tuple[StoredRun, ...]:
    return tuple(_run(index + 1, value) for index, value in enumerate(values))


class TestSameContextSegment:
    def test_uniform_history_is_one_segment(self) -> None:
        runs = _history(100.0, 101.0, 102.0)
        assert same_context_segment(runs) == runs

    def test_segment_stops_at_the_nearest_context_change(self) -> None:
        runs = (
            _run(1, 100.0, package="0.91.0"),
            _run(2, 101.0),
            _run(3, 102.0),
        )
        assert [run.run_id for run in same_context_segment(runs)] == [2, 3]

    def test_cpu_and_governor_changes_also_break_the_segment(self) -> None:
        for first in (
            _run(1, 100.0, cpu="Other CPU"),
            _run(1, 100.0, governor="powersave"),
        ):
            runs = (first, _run(2, 101.0), _run(3, 102.0))
            assert [run.run_id for run in same_context_segment(runs)] == [2, 3]

    def test_matching_context_before_a_break_is_not_admitted(self) -> None:
        # A→B→A flips: only the trailing A block counts, the early A runs
        # are separated by the B run and stay out
        runs = (
            _run(1, 100.0),
            _run(2, 500.0, package="0.91.0"),
            _run(3, 101.0),
        )
        assert [run.run_id for run in same_context_segment(runs)] == [3]

    def test_empty_history_yields_an_empty_segment(self) -> None:
        assert same_context_segment(()) == ()


class TestAssessDrift:
    def test_steady_series_raises_no_finding(self) -> None:
        assessment = assess_drift(_history(100.0, 101.0, 99.0, 100.5, 100.2))

        assert assessment.findings == ()
        assert assessment.assessed == 1
        assert assessment.insufficient == ()
        assert assessment.segment_runs == 5

    def test_outlier_beyond_sigma_is_a_finding(self) -> None:
        values = (100.0, 101.0, 99.0, 100.5, 250.0)
        assessment = assess_drift(_history(*values))

        assert len(assessment.findings) == 1
        finding = assessment.findings[0]
        assert finding.probe == "event-store-append"
        assert finding.metric == "events_per_second"
        assert finding.latest == 250.0
        baseline = values[:-1]
        assert finding.baseline_mean == pytest.approx(statistics.fmean(baseline))
        assert finding.baseline_std == pytest.approx(statistics.stdev(baseline))
        assert finding.sigma_distance == pytest.approx(
            abs(250.0 - statistics.fmean(baseline)) / statistics.stdev(baseline)
        )
        assert finding.samples == 5

    def test_flat_baseline_flags_any_deviation_without_a_sigma(self) -> None:
        assessment = assess_drift(_history(100.0, 100.0, 100.0, 100.0, 100.1))

        assert len(assessment.findings) == 1
        assert assessment.findings[0].sigma_distance is None
        assert assessment.findings[0].baseline_std == 0.0

    def test_flat_baseline_and_flat_latest_is_clean(self) -> None:
        assessment = assess_drift(_history(100.0, 100.0, 100.0, 100.0, 100.0))

        assert assessment.findings == ()

    def test_short_series_is_reported_insufficient_not_gated(self) -> None:
        assessment = assess_drift(_history(100.0, 250.0, 260.0))

        assert assessment.findings == ()
        assert assessment.assessed == 0
        assert [(series.samples, series.required) for series in assessment.insufficient] == [
            (3, DEFAULT_MIN_SAMPLES)
        ]

    def test_context_break_resets_the_population(self) -> None:
        # five comparable-looking values, but the first three belong to an
        # older package: only two same-context samples remain — insufficient
        runs = (
            _run(1, 100.0, package="0.91.0"),
            _run(2, 100.0, package="0.91.0"),
            _run(3, 100.0, package="0.91.0"),
            _run(4, 100.0),
            _run(5, 250.0),
        )
        assessment = assess_drift(runs)

        assert assessment.findings == ()
        assert assessment.segment_runs == 2
        assert assessment.insufficient[0].samples == 2

    def test_metric_missing_from_some_runs_shortens_its_series_only(self) -> None:
        runs = (
            *_history(100.0, 101.0, 99.0, 100.5),
            _run(5, 100.2, extra_metrics={"p95_ms": 2.5}),
        )
        assessment = assess_drift(runs)

        # events_per_second has 5 samples and is gated; p95_ms has 1
        assert assessment.assessed == 1
        assert [(series.metric, series.samples) for series in assessment.insufficient] == [
            ("p95_ms", 1)
        ]

    def test_empty_history_assesses_nothing(self) -> None:
        assessment = assess_drift(())

        assert assessment.findings == ()
        assert assessment.assessed == 0
        assert assessment.segment_runs == 0

    def test_threshold_and_floor_are_validated(self) -> None:
        with pytest.raises(ValueError, match="sigma must be positive"):
            assess_drift(_history(1.0), sigma=0.0)
        with pytest.raises(ValueError, match=f"at least {MIN_SAMPLES_FLOOR}"):
            assess_drift(_history(1.0), min_samples=2)

    def test_custom_sigma_widens_or_narrows_the_gate(self) -> None:
        values = (100.0, 102.0, 98.0, 101.0, 106.0)
        strict = assess_drift(_history(*values), sigma=1.0)
        loose = assess_drift(_history(*values), sigma=10.0)

        assert len(strict.findings) == 1
        assert loose.findings == ()

    def test_defaults_are_the_documented_values(self) -> None:
        assessment = assess_drift(_history(100.0, 100.0, 100.0, 100.0, 100.0))

        assert assessment.sigma == DEFAULT_ALERT_SIGMA
        assert assessment.min_samples == DEFAULT_MIN_SAMPLES


class TestRenderings:
    def test_json_carries_findings_insufficient_and_the_note(self) -> None:
        payload = drift_to_json(assess_drift(_history(100.0, 101.0, 99.0, 100.5, 250.0)))

        assert payload["sigma"] == DEFAULT_ALERT_SIGMA
        assert payload["segment_runs"] == 5
        findings = payload["findings"]
        assert isinstance(findings, list)
        assert findings[0]["metric"] == "events_per_second"
        assert findings[0]["sigma_distance"] is not None
        assert payload["note"] == (
            "same-context statistics only; an insufficient series is never gated"
        )

    def test_human_rendering_names_the_drift_and_the_insufficient_series(self) -> None:
        runs = (
            *_history(100.0, 101.0, 99.0, 100.5),
            _run(5, 250.0, extra_metrics={"p95_ms": 2.5}),
        )
        text = render_drift_human(assess_drift(runs))

        assert "Drift alert: 1 finding(s) across 1 gated series" in text
        assert "DRIFT event-store-append events_per_second: 250.00 is" in text
        assert "sigma out" in text
        assert "insufficient samples for event-store-append p95_ms: 1 of 5" in text

    def test_flat_baseline_renders_without_a_sigma_number(self) -> None:
        text = render_drift_human(assess_drift(_history(100.0, 100.0, 100.0, 100.0, 100.1)))

        assert "off a flat baseline" in text

    def test_clean_assessment_renders_the_summary_line_only(self) -> None:
        text = render_drift_human(assess_drift(_history(100.0, 101.0, 99.0, 100.5, 100.2)))

        assert text.startswith("Drift alert: 0 finding(s)")
        assert "\n" not in text
