# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fleet scorecard metric-projection regressions
"""Exercise every scorecard metric family through existing report APIs."""

from __future__ import annotations

import pytest

from synapse_channel.benchmark.trend import StoredRun
from synapse_channel.core.accounting import ModelPrice, build_accounting_report, format_usage_note
from synapse_channel.core.causality import build_causal_graph
from synapse_channel.core.causality_otel import build_otel_projection
from synapse_channel.core.fleet_scorecard_metrics import MetricPoint, fleet_metric_points
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.reliability import build_reliability_report
from synapse_channel.core.yield_advice import advise_yields


def _events() -> tuple[StoredEvent, ...]:
    return (
        StoredEvent(
            1,
            1.0,
            EventKind.CLAIM,
            {
                "task_id": "A",
                "owner": "alice",
                "status": "claimed",
                "paths": ["src/shared.py"],
                "worktree": "main",
                "lease_expires_at": 2.0,
            },
        ),
        StoredEvent(
            2,
            2.0,
            EventKind.CLAIM,
            {
                "task_id": "B",
                "owner": "bob",
                "status": "claimed",
                "paths": ["src/shared.py"],
                "worktree": "main",
                "lease_expires_at": 3.0,
            },
        ),
        StoredEvent(
            3,
            3.0,
            EventKind.LEDGER_PROGRESS,
            {
                "task_id": "A",
                "author": "alice",
                "kind": "usage",
                "text": format_usage_note(
                    model="model-a",
                    calls=2,
                    input_tokens=1000,
                    output_tokens=500,
                ),
            },
        ),
        StoredEvent(
            4,
            4.0,
            EventKind.LEDGER_PROGRESS,
            {
                "task_id": "B",
                "author": "bob",
                "kind": "verification",
                "text": "known_failures=one",
            },
        ),
    )


def _project(runs: tuple[StoredRun, ...] | None) -> tuple[MetricPoint, ...]:
    events = _events()
    return fleet_metric_points(
        causality=build_otel_projection(events),
        accounting=build_accounting_report(
            events,
            pricing={"model-a": ModelPrice(2.0, 4.0)},
            budgets={"alice": 3.0},
        ),
        conflicts=tuple(advise_yields(build_causal_graph(list(events)))),
        reliability=build_reliability_report(events, as_of=4.0),
        benchmark_runs=runs,
    )


def _by_name(points: tuple[MetricPoint, ...], name: str) -> list[MetricPoint]:
    return [point for point in points if point.name == name]


def test_projection_is_sorted_and_carries_every_evidence_family() -> None:
    latest = StoredRun(
        2,
        20.0,
        "0.99.1",
        "CPU",
        "performance",
        {"append": {"events_per_second": 125.0}},
    )
    previous = StoredRun(
        1,
        10.0,
        "0.99.1",
        "CPU",
        "performance",
        {"append": {"events_per_second": 100.0}},
    )

    points = _project((previous, latest))

    keys = [(point.name, point.attributes) for point in points]
    assert keys == sorted(keys)
    assert _by_name(points, "synapse.fleet.causality.traces")
    assert _by_name(points, "synapse.fleet.accounting.calls")
    assert _by_name(points, "synapse.fleet.conflict.present")
    assert _by_name(points, "synapse.fleet.reliability.findings")
    change = _by_name(points, "synapse.fleet.benchmark.relative_change")
    assert len(change) == 1
    assert change[0].value == pytest.approx(0.25)


@pytest.mark.parametrize(
    "runs",
    [
        None,
        (),
        (
            StoredRun(1, 1.0, "v", "cpu", "performance", {"p": {"m": 0.0}}),
            StoredRun(2, 2.0, "v", "cpu", "performance", {"p": {"m": 4.0}}),
        ),
        (
            StoredRun(1, 1.0, "v", "other", "performance", {"p": {"m": 2.0}}),
            StoredRun(2, 2.0, "v", "cpu", "performance", {"p": {"m": 4.0}}),
        ),
    ],
)
def test_no_relative_change_without_a_nonzero_matching_prior(
    runs: tuple[StoredRun, ...] | None,
) -> None:
    points = _project(runs)

    assert not _by_name(points, "synapse.fleet.benchmark.relative_change")
    if runs:
        assert _by_name(points, "synapse.fleet.benchmark.latest")
    else:
        assert not _by_name(points, "synapse.fleet.benchmark.latest")
