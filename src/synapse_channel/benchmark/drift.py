# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — statistical drift gate over the benchmark trend history
"""Gate the latest benchmark run against its own same-context history.

``--compare`` gates one run against one saved baseline; the trend store
watches the long arc but only renders it. This module closes the gap KIMI's
review named: a **deterministic statistical gate** over the stored history —
the latest run's value per probe metric is measured in sigma distances from
the mean of its predecessors, and a value further out than the threshold is
a drift finding the CLI turns into a non-zero exit.

Honest scope, same doctrine as the scorecard: numbers from different host
contexts do not form one population, so only the **trailing same-context
segment** — stored runs whose package version, CPU model, and governor all
match the latest run's — is admitted, and a series with fewer samples than
the floor is reported as *insufficient*, never silently gated. The
statistics are the sample mean and sample standard deviation of the
predecessors (the latest value is the measurand, not part of its own
baseline); a perfectly flat baseline has no sigma to measure against, so
any deviation from it is a finding with the distance reported as flat.
Everything is computed from stored values — deterministic and replayable,
no wall clock, no randomness.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from synapse_channel.benchmark.trend import StoredRun

DEFAULT_ALERT_SIGMA = 3.0
"""Sigma distance beyond which the latest value counts as drift."""

DEFAULT_MIN_SAMPLES = 5
"""Same-context samples (latest included) a series needs before it is gated."""

MIN_SAMPLES_FLOOR = 3
"""Hard floor: two predecessors are the least a standard deviation needs."""


@dataclass(frozen=True)
class DriftFinding:
    """One probe metric whose latest value drifted out of its own history.

    Attributes
    ----------
    probe : str
        The probe the metric belongs to.
    metric : str
        The metric name.
    latest : float
        The latest run's value — the measurand.
    baseline_mean : float
        Sample mean of the same-context predecessor values.
    baseline_std : float
        Sample standard deviation of those predecessors; ``0.0`` for a
        perfectly flat baseline.
    sigma_distance : float or None
        ``|latest - mean| / std``, or ``None`` when the baseline is flat
        and the distance has no sigma to be measured in.
    samples : int
        Same-context values in the series, the latest included.
    """

    probe: str
    metric: str
    latest: float
    baseline_mean: float
    baseline_std: float
    sigma_distance: float | None
    samples: int


@dataclass(frozen=True)
class InsufficientSeries:
    """A probe metric with too few same-context samples to gate honestly.

    Attributes
    ----------
    probe : str
        The probe the metric belongs to.
    metric : str
        The metric name.
    samples : int
        Same-context values available, the latest included.
    required : int
        The sample floor the assessment ran with.
    """

    probe: str
    metric: str
    samples: int
    required: int


@dataclass(frozen=True)
class DriftAssessment:
    """The drift picture of the latest stored run.

    Attributes
    ----------
    findings : tuple[DriftFinding, ...]
        Metrics whose latest value lies beyond the sigma threshold.
    insufficient : tuple[InsufficientSeries, ...]
        Metrics with too few same-context samples — reported, never
        silently gated.
    assessed : int
        Series that had enough samples and were measured.
    segment_runs : int
        Stored runs in the trailing same-context segment.
    sigma : float
        The threshold the assessment used.
    min_samples : int
        The sample floor the assessment used.
    """

    findings: tuple[DriftFinding, ...]
    insufficient: tuple[InsufficientSeries, ...]
    assessed: int
    segment_runs: int
    sigma: float
    min_samples: int


def same_context_segment(runs: tuple[StoredRun, ...]) -> tuple[StoredRun, ...]:
    """Return the trailing runs sharing the latest run's host and package context.

    Walking back from the latest run, the segment ends at the first run whose
    package version, CPU model, or governor differs — the same three fields
    the trend rendering annotates as context breaks, so the gate and the
    annotation can never disagree about where comparability ends.
    """
    if not runs:
        return ()
    latest = runs[-1]
    segment: list[StoredRun] = []
    for run in reversed(runs):
        if (
            run.package_version != latest.package_version
            or run.cpu_model != latest.cpu_model
            or run.governor != latest.governor
        ):
            break
        segment.append(run)
    return tuple(reversed(segment))


def assess_drift(
    runs: tuple[StoredRun, ...],
    *,
    sigma: float = DEFAULT_ALERT_SIGMA,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> DriftAssessment:
    """Measure the latest run's metrics against their same-context history.

    Parameters
    ----------
    runs : tuple[StoredRun, ...]
        The stored history, oldest first, the run under assessment last.
    sigma : float
        Sigma distance beyond which a value counts as drift; must be
        positive.
    min_samples : int
        Same-context samples (latest included) a series needs to be
        gated; at least :data:`MIN_SAMPLES_FLOOR`.

    Returns
    -------
    DriftAssessment
        Findings, insufficient series, and the parameters used.

    Raises
    ------
    ValueError
        If ``sigma`` is not positive or ``min_samples`` is below the floor.
    """
    if sigma <= 0:
        msg = "sigma must be positive"
        raise ValueError(msg)
    if min_samples < MIN_SAMPLES_FLOOR:
        msg = f"min_samples must be at least {MIN_SAMPLES_FLOOR}"
        raise ValueError(msg)
    segment = same_context_segment(runs)
    findings: list[DriftFinding] = []
    insufficient: list[InsufficientSeries] = []
    assessed = 0
    if not segment:
        return DriftAssessment(
            findings=(),
            insufficient=(),
            assessed=0,
            segment_runs=0,
            sigma=sigma,
            min_samples=min_samples,
        )
    latest = segment[-1]
    for probe in sorted(latest.metrics):
        for metric in sorted(latest.metrics[probe]):
            values = [
                run.metrics[probe][metric]
                for run in segment
                if metric in run.metrics.get(probe, {})
            ]
            if len(values) < min_samples:
                insufficient.append(
                    InsufficientSeries(
                        probe=probe,
                        metric=metric,
                        samples=len(values),
                        required=min_samples,
                    )
                )
                continue
            assessed += 1
            baseline = values[:-1]
            current = values[-1]
            mean = statistics.fmean(baseline)
            std = statistics.stdev(baseline)
            if std > 0.0:
                distance = abs(current - mean) / std
                if distance > sigma:
                    findings.append(
                        DriftFinding(
                            probe=probe,
                            metric=metric,
                            latest=current,
                            baseline_mean=mean,
                            baseline_std=std,
                            sigma_distance=distance,
                            samples=len(values),
                        )
                    )
            elif current != mean:
                findings.append(
                    DriftFinding(
                        probe=probe,
                        metric=metric,
                        latest=current,
                        baseline_mean=mean,
                        baseline_std=0.0,
                        sigma_distance=None,
                        samples=len(values),
                    )
                )
    return DriftAssessment(
        findings=tuple(findings),
        insufficient=tuple(insufficient),
        assessed=assessed,
        segment_runs=len(segment),
        sigma=sigma,
        min_samples=min_samples,
    )


def drift_to_json(assessment: DriftAssessment) -> dict[str, object]:
    """Return a stable JSON-compatible representation of the assessment."""
    return {
        "sigma": assessment.sigma,
        "min_samples": assessment.min_samples,
        "segment_runs": assessment.segment_runs,
        "assessed": assessment.assessed,
        "findings": [
            {
                "probe": finding.probe,
                "metric": finding.metric,
                "latest": finding.latest,
                "baseline_mean": finding.baseline_mean,
                "baseline_std": finding.baseline_std,
                "sigma_distance": finding.sigma_distance,
                "samples": finding.samples,
            }
            for finding in assessment.findings
        ],
        "insufficient": [
            {
                "probe": series.probe,
                "metric": series.metric,
                "samples": series.samples,
                "required": series.required,
            }
            for series in assessment.insufficient
        ],
        "note": "same-context statistics only; an insufficient series is never gated",
    }


def render_drift_human(assessment: DriftAssessment) -> str:
    """Render the assessment as compact operator text."""
    lines = [
        f"Drift alert: {len(assessment.findings)} finding(s) across "
        f"{assessment.assessed} gated series "
        f"({assessment.segment_runs} same-context run(s), "
        f"sigma {assessment.sigma:g}, floor {assessment.min_samples})"
    ]
    for finding in assessment.findings:
        distance = (
            f"{finding.sigma_distance:.1f} sigma out"
            if finding.sigma_distance is not None
            else "off a flat baseline"
        )
        lines.append(
            f"DRIFT {finding.probe} {finding.metric}: {finding.latest:,.2f} is {distance} "
            f"(baseline mean {finding.baseline_mean:,.2f}, std {finding.baseline_std:,.2f}, "
            f"{finding.samples} samples)"
        )
    for series in assessment.insufficient:
        lines.append(
            f"insufficient samples for {series.probe} {series.metric}: "
            f"{series.samples} of {series.required} same-context run(s) — not gated"
        )
    return "\n".join(lines)
