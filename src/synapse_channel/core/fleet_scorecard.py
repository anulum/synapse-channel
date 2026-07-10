# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — compose durable fleet evidence into one exportable scorecard
"""Compose existing fleet evidence into one offline-first scorecard.

The scorecard is a projection, not a new telemetry plane. It reuses the durable
causality, opt-in accounting, contention, reliability, and benchmark-history
readers and preserves their honesty boundaries. No model call, live-hub query,
claim mutation, or pricing default is introduced here. The result contains the
full source reports for portable JSON export plus a small set of numeric metric
points for an OTLP collector.

Benchmark history stays complete in the JSON bundle. The metric projection emits
the latest value and, only when a prior run has matching package and host context,
its relative change. It deliberately does not pretend that a one-shot metrics
push can backfill historical timestamps.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.benchmark.trend import StoredRun, load_history, trend_to_json
from synapse_channel.core.accounting import (
    AccountingReport,
    ModelPrice,
    accounting_to_json,
    run_accounting_report,
)
from synapse_channel.core.causality import DEFAULT_MAX_GRAPH_NODES
from synapse_channel.core.causality_otel import (
    SERVICE_NAME,
    OtelProjection,
    projection_to_json,
    run_otel_projection,
)
from synapse_channel.core.fleet_scorecard_metrics import MetricPoint, fleet_metric_points
from synapse_channel.core.reliability import (
    ReliabilityReport,
    reliability_to_json,
    run_reliability_report,
)
from synapse_channel.core.yield_advice import (
    YieldAdvice,
    advice_to_json,
    run_yield_advice,
)

SCORECARD_SCHEMA_VERSION = 1
"""Version of the portable fleet-scorecard JSON shape."""


@dataclass(frozen=True)
class FleetScorecard:
    """One composed fleet evidence bundle.

    Attributes
    ----------
    generated_from_seq : int
        Highest durable sequence represented by the source reports.
    as_of : float
        Latest durable event timestamp represented by the reports.
    causality : OtelProjection
        Existing deterministic causality span projection.
    accounting : AccountingReport
        Existing opt-in usage and cost evidence.
    conflicts : tuple[YieldAdvice, ...]
        Existing advisory overlap analysis.
    reliability : ReliabilityReport
        Existing evidence-only reliability findings.
    benchmark_runs : tuple[StoredRun, ...] or None
        Optional complete benchmark history. ``None`` means the operator did
        not supply a trend store; an empty tuple means the supplied store had
        no runs.
    metrics : tuple[MetricPoint, ...]
        Numeric collector projection derived from the reports above.
    """

    generated_from_seq: int
    as_of: float
    causality: OtelProjection
    accounting: AccountingReport
    conflicts: tuple[YieldAdvice, ...]
    reliability: ReliabilityReport
    benchmark_runs: tuple[StoredRun, ...] | None
    metrics: tuple[MetricPoint, ...]


def build_fleet_scorecard(
    *,
    causality: OtelProjection,
    accounting: AccountingReport,
    conflicts: tuple[YieldAdvice, ...],
    reliability: ReliabilityReport,
    benchmark_runs: tuple[StoredRun, ...] | None = None,
) -> FleetScorecard:
    """Compose already-built evidence reports into a fleet scorecard.

    Parameters
    ----------
    causality : OtelProjection
        Deterministic task-span projection.
    accounting : AccountingReport
        Opt-in accounting report.
    conflicts : tuple[YieldAdvice, ...]
        Advisory overlapping-claim analysis.
    reliability : ReliabilityReport
        Evidence-only reliability report.
    benchmark_runs : tuple[StoredRun, ...] or None, optional
        Optional benchmark history.

    Returns
    -------
    FleetScorecard
        The composed bundle and its numeric metric projection.
    """
    metrics = fleet_metric_points(
        causality=causality,
        accounting=accounting,
        conflicts=conflicts,
        reliability=reliability,
        benchmark_runs=benchmark_runs,
    )
    return FleetScorecard(
        generated_from_seq=max(
            accounting.generated_from_seq,
            reliability.generated_from_seq,
        ),
        as_of=max(accounting.as_of, reliability.as_of),
        causality=causality,
        accounting=accounting,
        conflicts=conflicts,
        reliability=reliability,
        benchmark_runs=benchmark_runs,
        metrics=metrics,
    )


def run_fleet_scorecard(
    db_path: str | Path,
    *,
    trend_path: str | Path | None = None,
    pricing: Mapping[str, ModelPrice] | None = None,
    budgets: Mapping[str, float] | None = None,
    max_nodes: int | None = DEFAULT_MAX_GRAPH_NODES,
    service_name: str = SERVICE_NAME,
    key_file: str | Path | None = None,
) -> FleetScorecard:
    """Build every fleet-scorecard component from durable local stores.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Hub event-store database read by every durable report.
    trend_path : str or pathlib.Path or None, optional
        Optional benchmark-history SQLite store.
    pricing : collections.abc.Mapping[str, ModelPrice] or None, optional
        Local pricing table for the existing accounting projection.
    budgets : collections.abc.Mapping[str, float] or None, optional
        Local per-agent budget evidence.
    max_nodes : int or None, optional
        Fail-closed causality and contention graph ceiling. ``None`` or ``0``
        lifts it, matching the existing causality commands.
    service_name : str, optional
        OpenTelemetry ``service.name`` stamped on causality spans.
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key for an encrypted event store.

    Returns
    -------
    FleetScorecard
        The composed local evidence bundle.

    Raises
    ------
    ValueError
        If a required store is absent, encrypted without its key, exceeds the
        graph ceiling, or contains an invalid report shape.
    """
    accounting = run_accounting_report(
        db_path,
        pricing=pricing,
        budgets=budgets,
        key_file=key_file,
    )
    reliability = run_reliability_report(db_path, key_file=key_file)
    conflicts = tuple(run_yield_advice(db_path, max_nodes=max_nodes, key_file=key_file))
    causality = run_otel_projection(
        db_path,
        max_nodes=max_nodes,
        service_name=service_name,
        key_file=key_file,
    )
    history = None if trend_path is None else load_history(trend_path)
    return build_fleet_scorecard(
        causality=causality,
        accounting=accounting,
        conflicts=conflicts,
        reliability=reliability,
        benchmark_runs=history,
    )


def fleet_scorecard_to_json(scorecard: FleetScorecard) -> dict[str, object]:
    """Return the stable, portable JSON representation of ``scorecard``."""
    return {
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "generated_from_seq": scorecard.generated_from_seq,
        "as_of": scorecard.as_of,
        "causality": projection_to_json(scorecard.causality),
        "accounting": accounting_to_json(scorecard.accounting),
        "conflicts": advice_to_json(list(scorecard.conflicts)),
        "reliability": reliability_to_json(scorecard.reliability),
        "benchmark_trend": (
            None if scorecard.benchmark_runs is None else trend_to_json(scorecard.benchmark_runs)
        ),
        "metrics": [metric_point_to_json(point) for point in scorecard.metrics],
        "note": (
            "offline durable evidence; accounting is opt-in, contention is advisory, "
            "and benchmark values are host-dependent"
        ),
    }


def metric_point_to_json(point: MetricPoint) -> dict[str, object]:
    """Return one metric point as stable JSON-compatible data."""
    return {
        "name": point.name,
        "value": point.value,
        "unit": point.unit,
        "description": point.description,
        "attributes": dict(point.attributes),
    }
