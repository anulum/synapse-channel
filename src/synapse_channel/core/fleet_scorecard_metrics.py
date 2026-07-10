# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — project fleet evidence reports onto OpenTelemetry metric points
"""Project composed fleet evidence onto deterministic numeric metric points.

This module owns only the metric plane of the fleet scorecard. It converts the
existing causality, accounting, contention, reliability, and benchmark reports
into SDK-independent gauge records. Collector transport remains in
:mod:`synapse_channel.otel_metrics_export`, while the full source-report JSON
stays in :mod:`synapse_channel.core.fleet_scorecard`.

Benchmark history is not backfilled through a one-shot gauge export. The metric
projection carries the latest value and a relative change only when a prior run
has the same package, CPU, and governor; the portable scorecard retains the full
history and every context break.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from synapse_channel.core.accounting import AccountingReport, AccountingTotals, UsageSummary
from synapse_channel.core.causality_otel import OtelProjection
from synapse_channel.core.reliability import ReliabilityReport
from synapse_channel.core.yield_advice import YieldAdvice


@dataclass(frozen=True)
class MetricPoint:
    """One numeric observation ready for an OpenTelemetry gauge.

    Attributes
    ----------
    name : str
        Stable dotted metric name.
    value : int or float
        Numeric gauge value.
    unit : str
        UCUM-compatible unit or an annotation unit in braces.
    description : str
        Human-readable meaning of the observation.
    attributes : tuple[tuple[str, str], ...]
        Sorted dimensions attached to the point.
    """

    name: str
    value: int | float
    unit: str
    description: str
    attributes: tuple[tuple[str, str], ...] = ()


class BenchmarkRun(Protocol):
    """Structural benchmark-history record consumed by the core projection.

    The benchmark feature layer owns storage and its concrete ``StoredRun``
    dataclass. Core needs only these immutable data fields, so accepting a
    protocol keeps the package dependency pointed inward without duplicating
    the feature type.
    """

    @property
    def run_id(self) -> int:  # pragma: no cover - structural
        """Return the monotonically increasing store row id."""
        ...

    @property
    def started_at(self) -> float:  # pragma: no cover - structural
        """Return the run start timestamp."""
        ...

    @property
    def package_version(self) -> str:  # pragma: no cover - structural
        """Return the measured package version."""
        ...

    @property
    def cpu_model(self) -> str:  # pragma: no cover - structural
        """Return the host CPU model."""
        ...

    @property
    def governor(self) -> str:  # pragma: no cover - structural
        """Return the host frequency governor."""
        ...

    @property
    def metrics(self) -> dict[str, dict[str, float]]:  # pragma: no cover - structural
        """Return probe-to-metric numeric values."""
        ...


def fleet_metric_points(
    *,
    causality: OtelProjection,
    accounting: AccountingReport,
    conflicts: tuple[YieldAdvice, ...],
    reliability: ReliabilityReport,
    benchmark_runs: tuple[BenchmarkRun, ...] | None,
) -> tuple[MetricPoint, ...]:
    """Return the sorted numeric projection of every scorecard component.

    Parameters
    ----------
    causality : OtelProjection
        Deterministic task trace projection.
    accounting : AccountingReport
        Opt-in usage and cost evidence.
    conflicts : tuple[YieldAdvice, ...]
        Advisory overlapping-claim pairs.
    reliability : ReliabilityReport
        Evidence-only operational findings.
    benchmark_runs : tuple[BenchmarkRun, ...] or None
        Optional complete benchmark history.

    Returns
    -------
    tuple[MetricPoint, ...]
        Gauge points sorted by metric name and attributes.
    """
    return tuple(
        sorted(
            (
                *_causality_metrics(causality),
                *_accounting_metrics(accounting),
                *_conflict_metrics(conflicts),
                *_reliability_metrics(reliability),
                *_benchmark_metrics(benchmark_runs),
            ),
            key=lambda point: (point.name, point.attributes),
        )
    )


def _point(
    name: str,
    value: int | float,
    unit: str,
    description: str,
    **attributes: str,
) -> MetricPoint:
    """Build one point with deterministically ordered string attributes."""
    return MetricPoint(
        name=name,
        value=value,
        unit=unit,
        description=description,
        attributes=tuple(sorted(attributes.items())),
    )


def _causality_metrics(projection: OtelProjection) -> tuple[MetricPoint, ...]:
    """Return trace-volume observations for the causality projection."""
    return (
        _point(
            "synapse.fleet.causality.traces",
            projection.trace_count,
            "{trace}",
            "Task traces in the current durable causality projection.",
        ),
        _point(
            "synapse.fleet.causality.spans",
            len(projection.spans),
            "{span}",
            "Spans in the current durable causality projection.",
        ),
        _point(
            "synapse.fleet.causality.skipped_events",
            projection.skipped_events,
            "{event}",
            "Taskless events omitted from the task-trace projection.",
        ),
    )


def _accounting_metrics(report: AccountingReport) -> tuple[MetricPoint, ...]:
    """Return fleet, agent, model, and budget accounting observations."""
    points: list[MetricPoint] = []
    points.extend(_usage_points(report.totals, {}))
    for summary in report.agents:
        points.extend(_usage_points(summary, {"agent": summary.key}))
    for summary in report.models:
        points.extend(_usage_points(summary, {"model": summary.key}))
    for status in report.budgets:
        attributes = {"agent": status.agent}
        points.extend(
            (
                _point(
                    "synapse.fleet.accounting.budget",
                    status.budget,
                    "{currency_unit}",
                    "Locally declared spend ceiling; evidence, not enforcement.",
                    **attributes,
                ),
                _point(
                    "synapse.fleet.accounting.budget_remaining",
                    status.remaining,
                    "{currency_unit}",
                    "Non-negative remainder under the locally declared spend ceiling.",
                    **attributes,
                ),
                _point(
                    "synapse.fleet.accounting.over_budget",
                    int(status.over_budget),
                    "1",
                    "Whether opt-in estimated spend reached the local ceiling.",
                    **attributes,
                ),
            )
        )
    return tuple(points)


def _usage_points(
    summary: AccountingTotals | UsageSummary,
    attributes: dict[str, str],
) -> tuple[MetricPoint, ...]:
    """Return the four common accounting gauges for a typed summary object."""
    return (
        _point(
            "synapse.fleet.accounting.calls",
            summary.calls,
            "{call}",
            "Opt-in model calls represented by durable usage notes.",
            **attributes,
        ),
        _point(
            "synapse.fleet.accounting.input_tokens",
            summary.input_tokens,
            "{token}",
            "Opt-in input tokens represented by durable usage notes.",
            **attributes,
        ),
        _point(
            "synapse.fleet.accounting.output_tokens",
            summary.output_tokens,
            "{token}",
            "Opt-in output tokens represented by durable usage notes.",
            **attributes,
        ),
        _point(
            "synapse.fleet.accounting.estimated_cost",
            summary.estimated_cost,
            "{currency_unit}",
            "Opt-in recorded or locally priced model cost evidence.",
            **attributes,
        ),
    )


def _conflict_metrics(conflicts: tuple[YieldAdvice, ...]) -> tuple[MetricPoint, ...]:
    """Return the overlap total and pair-labelled conflict heatmap points."""
    points = [
        _point(
            "synapse.fleet.conflicts",
            len(conflicts),
            "{pair}",
            "Overlapping live-claim pairs in the current durable projection.",
        )
    ]
    for advice in conflicts:
        points.append(
            _point(
                "synapse.fleet.conflict.present",
                1,
                "1",
                "Advisory live-claim conflict pair; no claim was preempted.",
                holder_owner=advice.holder.owner,
                holder_task=advice.holder.task_id,
                yielder_owner=advice.yielder.owner,
                yielder_task=advice.yielder.task_id,
            )
        )
    return tuple(points)


def _reliability_metrics(report: ReliabilityReport) -> tuple[MetricPoint, ...]:
    """Return evidence-only reliability counts, never ranks or scores."""
    counts = Counter((finding.kind, finding.owner) for finding in report.findings)
    points = [
        _point(
            "synapse.fleet.reliability.findings",
            len(report.findings),
            "{finding}",
            "Evidence-only reliability findings in the durable report.",
        )
    ]
    points.extend(
        _point(
            "synapse.fleet.reliability.findings",
            count,
            "{finding}",
            "Evidence-only reliability findings in the durable report.",
            kind=kind,
            owner=owner,
        )
        for (kind, owner), count in sorted(counts.items())
    )
    return tuple(points)


def _benchmark_metrics(runs: tuple[BenchmarkRun, ...] | None) -> tuple[MetricPoint, ...]:
    """Return latest benchmark values and comparable relative changes."""
    if not runs:
        return ()
    latest = runs[-1]
    points: list[MetricPoint] = []
    for probe in sorted(latest.metrics):
        for metric in sorted(latest.metrics[probe]):
            attributes = {
                "cpu_model": latest.cpu_model,
                "governor": latest.governor,
                "metric": metric,
                "package_version": latest.package_version,
                "probe": probe,
                "run_id": str(latest.run_id),
            }
            value = latest.metrics[probe][metric]
            points.append(
                _point(
                    "synapse.fleet.benchmark.latest",
                    value,
                    "1",
                    "Latest host-dependent benchmark value from the supplied trend store.",
                    **attributes,
                )
            )
            previous = _previous_comparable(runs[:-1], latest, probe, metric)
            if previous is not None and previous != 0.0:
                points.append(
                    _point(
                        "synapse.fleet.benchmark.relative_change",
                        (value - previous) / abs(previous),
                        "1",
                        "Relative change from the previous matching package and host context.",
                        **attributes,
                    )
                )
    return tuple(points)


def _previous_comparable(
    candidates: tuple[BenchmarkRun, ...],
    latest: BenchmarkRun,
    probe: str,
    metric: str,
) -> float | None:
    """Return the newest prior value from the same package and host context."""
    for run in reversed(candidates):
        if (
            run.package_version == latest.package_version
            and run.cpu_model == latest.cpu_model
            and run.governor == latest.governor
            and metric in run.metrics.get(probe, {})
        ):
            return run.metrics[probe][metric]
    return None
