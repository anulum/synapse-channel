# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — render log-derived signals as node_exporter textfile metrics
"""Project the store-derived analytics into Prometheus textfile metrics.

The hub's ``/metrics`` endpoint answers *what the live process is doing*; the
reliability report and the causal-health assessment answer *what the durable
log reveals happened*. This module bridges the second plane into the first: it
renders those two reports as Prometheus exposition text sized for the
``node_exporter`` **textfile collector** — a timer writes a ``.prom`` file into
the collector's directory, node_exporter serves it, and the log-derived signals
land in the same Prometheus and alerting plane as the live counters without the
hub needing to be up.

Unlike :func:`synapse_channel.core.metrics.render_prometheus`, these families
carry **labels** (per owner, per anomaly kind), so each emits one ``HELP``/
``TYPE`` header followed by one labelled sample per series — the shape a
node_exporter textfile is expected to have. Every value is derived from the
report deterministically, so the same log renders the same file.
"""

from __future__ import annotations

from collections.abc import Iterable

from synapse_channel.core.causality_health import CausalHealthReport
from synapse_channel.core.reliability import ReliabilityReport

_RELIABILITY_FINDING_KINDS = (
    "stale_claim",
    "declared_failed_check",
    "broken_handoff",
    "conflict_pair",
)
"""The reliability finding kinds, emitted even at zero so a series never vanishes."""


def _escape_label(value: str) -> str:
    """Escape a label value for the Prometheus exposition format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _sample(name: str, labels: dict[str, str], value: float) -> str:
    """Render one labelled sample line."""
    if labels:
        rendered = ",".join(f'{key}="{_escape_label(val)}"' for key, val in labels.items())
        return f"{name}{{{rendered}}} {_format_value(value)}"
    return f"{name} {_format_value(value)}"


def _format_value(value: float) -> str:
    """Render a value, dropping the decimal point for an integral number."""
    number = float(value)
    return str(int(number)) if number.is_integer() else repr(number)


def _family(name: str, help_text: str, metric_type: str, samples: Iterable[str]) -> list[str]:
    """Render one metric family: a HELP line, a TYPE line, then its samples."""
    return [f"# HELP {name} {help_text}", f"# TYPE {name} {metric_type}", *samples]


def render_reliability_textfile(report: ReliabilityReport) -> str:
    """Render a reliability report as node_exporter textfile exposition.

    Emits the total finding count per kind (always all four kinds, zero
    included, so an alert on a series can fire the first time it goes
    positive), the per-owner finding counts, and the report's watermark
    sequence — an evidence projection, never a grade.
    """
    counts_by_kind = dict.fromkeys(_RELIABILITY_FINDING_KINDS, 0)
    for finding in report.findings:
        if finding.kind in counts_by_kind:
            counts_by_kind[finding.kind] += 1

    lines: list[str] = []
    lines += _family(
        "synapse_reliability_findings",
        "Reliability findings in the durable log, by evidence kind.",
        "gauge",
        (
            _sample("synapse_reliability_findings", {"kind": kind}, count)
            for kind, count in counts_by_kind.items()
        ),
    )
    lines += _family(
        "synapse_reliability_owner_findings",
        "Reliability findings attributed to each owner, by evidence kind.",
        "gauge",
        (
            line
            for owner in report.owners
            for line in (
                _sample(
                    "synapse_reliability_owner_findings",
                    {"owner": owner.owner, "kind": "stale_claim"},
                    owner.stale_claims,
                ),
                _sample(
                    "synapse_reliability_owner_findings",
                    {"owner": owner.owner, "kind": "declared_failed_check"},
                    owner.declared_failed_checks,
                ),
                _sample(
                    "synapse_reliability_owner_findings",
                    {"owner": owner.owner, "kind": "broken_handoff"},
                    owner.broken_handoffs,
                ),
                _sample(
                    "synapse_reliability_owner_findings",
                    {"owner": owner.owner, "kind": "conflict_pair"},
                    owner.conflict_pairs,
                ),
            )
        ),
    )
    lines += _family(
        "synapse_reliability_generated_from_seq",
        "The event sequence the reliability report was built through.",
        "gauge",
        (_sample("synapse_reliability_generated_from_seq", {}, report.generated_from_seq),),
    )
    return "\n".join(lines) + "\n"


def render_health_textfile(report: CausalHealthReport) -> str:
    """Render a causal-health report as node_exporter textfile exposition.

    Emits the three anomaly counts under one labelled family, the total, and
    the tasks scanned — every value measured against the log's own final
    timestamp, so the file is deterministic over a given log.
    """
    lines: list[str] = []
    lines += _family(
        "synapse_causal_health_anomalies",
        "Causal-health anomalies in the durable log, by shape.",
        "gauge",
        (
            _sample("synapse_causal_health_anomalies", {"shape": "orphaned"}, len(report.orphaned)),
            _sample("synapse_causal_health_anomalies", {"shape": "dangling"}, len(report.dangling)),
            _sample("synapse_causal_health_anomalies", {"shape": "stale"}, len(report.stale)),
        ),
    )
    lines += _family(
        "synapse_causal_health_anomalies_total",
        "Total causal-health anomalies across all shapes.",
        "gauge",
        (_sample("synapse_causal_health_anomalies_total", {}, report.anomaly_count),),
    )
    lines += _family(
        "synapse_causal_health_tasks_scanned",
        "Tasks whose recorded lifecycle the health assessment walked.",
        "gauge",
        (_sample("synapse_causal_health_tasks_scanned", {}, report.tasks_scanned),),
    )
    return "\n".join(lines) + "\n"
