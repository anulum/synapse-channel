# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — scorecard baseline comparison for regression gating
"""Compare a benchmark run against a saved baseline scorecard.

This turns ``synapse benchmark`` from measurement into regression detection:
a run saved with ``--results`` becomes the baseline a later run is compared
against with ``--compare``, and a gated metric drifting past the tolerance
is a regression the exit code reports.

Two rules keep the comparison honest. **Hosts must match:** a baseline
recorded on a different CPU model is refused outright — cross-host deltas
would compare hardware, not the package — and softer context drift
(governor, interpreter, package version, platform) is reported as loud
warnings on the comparison itself. **Only directional metrics gate:**
throughput (``*_per_second``, higher is better) and latency percentiles
(``*_ms``, lower is better) can regress; everything else a probe reports
(byte ratios, rebuilt-claim counts) is context, not a gate. The default
tolerance is generous because the scorecard's own isolation label says so:
a shared-workstation run carries scheduler noise an isolated-core run
would not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.benchmark.scorecard import Scorecard

DEFAULT_TOLERANCE_PCT = 25.0
"""Default regression tolerance, sized for shared-workstation noise."""

HIGHER_IS_BETTER = "higher"
LOWER_IS_BETTER = "lower"

_SOFT_CONTEXT_FIELDS = ("governor", "python", "package_version", "platform")


@dataclass(frozen=True)
class MetricDelta:
    """One gated metric's movement between the baseline and the current run.

    Attributes
    ----------
    probe : str
        Probe the metric belongs to.
    metric : str
        Metric name, e.g. ``events_per_second`` or ``p95_ms``.
    baseline, current : float
        The two measured values.
    change_pct : float
        Signed relative change against the baseline, in percent.
    direction : str
        :data:`HIGHER_IS_BETTER` or :data:`LOWER_IS_BETTER`.
    regression : bool
        Whether the change moved past the tolerance in the bad direction.
    """

    probe: str
    metric: str
    baseline: float
    current: float
    change_pct: float
    direction: str
    regression: bool


@dataclass(frozen=True)
class ScorecardComparison:
    """The outcome of comparing one run against a saved baseline.

    Attributes
    ----------
    tolerance_pct : float
        Regression tolerance the deltas were gated with.
    context_warnings : tuple[str, ...]
        Soft host-context drift between the two runs (governor,
        interpreter, package version, platform) — loud annotations, not
        refusals.
    deltas : tuple[MetricDelta, ...]
        Every gated metric present in both runs, deterministic order.
    missing_probes : tuple[str, ...]
        Probes the baseline holds but the current run did not execute.
    new_probes : tuple[str, ...]
        Probes the current run executed but the baseline does not hold.
    """

    tolerance_pct: float
    context_warnings: tuple[str, ...]
    deltas: tuple[MetricDelta, ...]
    missing_probes: tuple[str, ...]
    new_probes: tuple[str, ...]

    @property
    def regressions(self) -> tuple[MetricDelta, ...]:
        """The deltas that moved past the tolerance in the bad direction."""
        return tuple(delta for delta in self.deltas if delta.regression)


def metric_direction(name: str) -> str:
    """Return which way a metric may safely move, or ``""`` when ungated."""
    if name.endswith("_per_second"):
        return HIGHER_IS_BETTER
    if name.endswith("_ms"):
        return LOWER_IS_BETTER
    return ""


def load_baseline(path: Path) -> dict[str, Any]:
    """Load and validate a scorecard JSON written by ``--results``.

    Raises
    ------
    ValueError
        When the file is unreadable, is not JSON, or lacks the scorecard
        shape (a ``context`` object carrying ``cpu_model`` and a
        ``results`` list of named probe entries).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read baseline: {exc}"
        raise ValueError(msg) from exc
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"baseline is not JSON: {path}: {exc}"
        raise ValueError(msg) from exc
    context = loaded.get("context") if isinstance(loaded, dict) else None
    results = loaded.get("results") if isinstance(loaded, dict) else None
    if (
        not isinstance(context, dict)
        or not isinstance(context.get("cpu_model"), str)
        or not isinstance(results, list)
        or not all(isinstance(entry, dict) and "name" in entry for entry in results)
    ):
        msg = f"baseline is not a scorecard (expected --results output): {path}"
        raise ValueError(msg)
    baseline: dict[str, Any] = loaded
    return baseline


def _baseline_metrics(baseline: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Index the baseline's numeric metrics by probe name."""
    indexed: dict[str, dict[str, float]] = {}
    for entry in baseline["results"]:
        metrics = entry.get("metrics")
        if not isinstance(metrics, dict):
            continue
        indexed[str(entry["name"])] = {
            str(name): float(value)
            for name, value in metrics.items()
            if isinstance(value, (int, float))
        }
    return indexed


def compare_scorecards(
    baseline: dict[str, Any],
    current: Scorecard,
    *,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
) -> ScorecardComparison:
    """Compare the current run against a loaded baseline scorecard.

    Parameters
    ----------
    baseline : dict[str, Any]
        A scorecard document from :func:`load_baseline`.
    current : Scorecard
        The run just measured.
    tolerance_pct : float, optional
        Allowed drift in percent before a gated metric counts as a
        regression.

    Raises
    ------
    ValueError
        When the baseline was recorded on a different CPU model — a
        cross-host comparison would compare hardware, not the package.
    """
    context = baseline["context"]
    if context["cpu_model"] != current.context.cpu_model:
        msg = (
            "baseline host does not match: baseline cpu_model "
            f"{context['cpu_model']!r} vs current {current.context.cpu_model!r}; "
            "record a fresh baseline on this host with --results"
        )
        raise ValueError(msg)
    warnings = tuple(
        f"{field} differs: baseline {context.get(field)!r} vs current "
        f"{getattr(current.context, field)!r}"
        for field in _SOFT_CONTEXT_FIELDS
        if context.get(field) != getattr(current.context, field)
    )
    indexed = _baseline_metrics(baseline)
    current_names = [result.name for result in current.results]
    deltas: list[MetricDelta] = []
    for result in current.results:
        base_metrics = indexed.get(result.name)
        if base_metrics is None:
            continue
        for metric in sorted(result.metrics):
            direction = metric_direction(metric)
            if not direction or metric not in base_metrics:
                continue
            base_value = base_metrics[metric]
            if base_value <= 0:
                continue
            current_value = result.metrics[metric]
            change_pct = (current_value - base_value) / base_value * 100.0
            regressed = (
                change_pct < -tolerance_pct
                if direction == HIGHER_IS_BETTER
                else change_pct > tolerance_pct
            )
            deltas.append(
                MetricDelta(
                    probe=result.name,
                    metric=metric,
                    baseline=base_value,
                    current=current_value,
                    change_pct=change_pct,
                    direction=direction,
                    regression=regressed,
                )
            )
    missing = tuple(sorted(set(indexed) - set(current_names)))
    new = tuple(sorted(set(current_names) - set(indexed)))
    return ScorecardComparison(
        tolerance_pct=tolerance_pct,
        context_warnings=warnings,
        deltas=tuple(deltas),
        missing_probes=missing,
        new_probes=new,
    )


def comparison_to_json(comparison: ScorecardComparison) -> dict[str, object]:
    """Return a stable JSON-compatible representation of one comparison."""
    return {
        "tolerance_pct": comparison.tolerance_pct,
        "context_warnings": list(comparison.context_warnings),
        "deltas": [
            {
                "probe": delta.probe,
                "metric": delta.metric,
                "baseline": delta.baseline,
                "current": delta.current,
                "change_pct": delta.change_pct,
                "direction": delta.direction,
                "regression": delta.regression,
            }
            for delta in comparison.deltas
        ],
        "missing_probes": list(comparison.missing_probes),
        "new_probes": list(comparison.new_probes),
        "regressed": bool(comparison.regressions),
    }


def render_comparison_human(comparison: ScorecardComparison) -> str:
    """Render one comparison as compact terminal text."""
    lines = [f"Baseline comparison (tolerance ±{comparison.tolerance_pct:g}%)"]
    lines.extend(f"WARNING: {warning}" for warning in comparison.context_warnings)
    for delta in comparison.deltas:
        verdict = "REGRESSION" if delta.regression else "ok"
        arrow = "higher-is-better" if delta.direction == HIGHER_IS_BETTER else "lower-is-better"
        lines.append(
            f"{delta.probe}/{delta.metric}: {delta.baseline:,.2f} -> "
            f"{delta.current:,.2f} ({delta.change_pct:+.1f}%, {arrow}) {verdict}"
        )
    if not comparison.deltas:
        lines.append("no gated metrics shared with the baseline")
    if comparison.missing_probes:
        lines.append("not run this time: " + ", ".join(comparison.missing_probes))
    if comparison.new_probes:
        lines.append("not in the baseline: " + ", ".join(comparison.new_probes))
    count = len(comparison.regressions)
    lines.append(
        f"{count} regression{'s' if count != 1 else ''} beyond "
        f"±{comparison.tolerance_pct:g}% tolerance"
    )
    return "\n".join(lines)
