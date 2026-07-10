# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — composed fleet scorecard regressions
"""Exercise the scorecard through real event and benchmark stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.benchmark.probes import ProbeResult
from synapse_channel.benchmark.scorecard import NON_ISOLATED_LABEL, HostContext, Scorecard
from synapse_channel.benchmark.trend import StoredRun, append_scorecard, load_history, trend_to_json
from synapse_channel.core.accounting import ModelPrice, format_usage_note
from synapse_channel.core.fleet_scorecard import (
    SCORECARD_SCHEMA_VERSION,
    FleetScorecard,
    build_fleet_scorecard,
    fleet_scorecard_to_json,
    metric_point_to_json,
    run_fleet_scorecard,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _seed_hub(path: Path) -> None:
    store = EventStore(path)
    store.append(
        EventKind.LEDGER_TASK,
        {"task_id": "A", "title": "first", "depends_on": [], "status": "open"},
        ts=1.0,
    )
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "A",
            "owner": "alice",
            "status": "claimed",
            "paths": ["src/shared.py"],
            "worktree": "main",
            "lease_expires_at": 2.0,
        },
        ts=2.0,
    )
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "B",
            "owner": "bob",
            "status": "claimed",
            "paths": ["src/shared.py"],
            "worktree": "main",
            "lease_expires_at": 3.0,
        },
        ts=3.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "A",
            "author": "alice",
            "kind": "usage",
            "text": format_usage_note(
                model="local-model",
                calls=2,
                input_tokens=1000,
                output_tokens=500,
            ),
        },
        ts=4.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "B",
            "author": "bob",
            "kind": "verification",
            "text": "known_failures=collector-timeout",
        },
        ts=5.0,
    )
    store.close()


def _benchmark(value: float, *, started_at: float) -> Scorecard:
    context = HostContext(
        package_version="0.99.1",
        python="3.12.3",
        platform="Linux-test",
        cpu_model="Test CPU",
        cpu_count=8,
        governor="performance",
        load_before=(0.1, 0.1, 0.1),
        load_after=(0.2, 0.1, 0.1),
        isolation=NON_ISOLATED_LABEL,
        started_at=started_at,
    )
    result = ProbeResult(
        name="event-store-append",
        iterations=10,
        duration_seconds=0.1,
        metrics={"events_per_second": value, "p95_ms": 2.5},
    )
    return Scorecard(context=context, results=(result,))


def _points(scorecard: FleetScorecard) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    return {(point.name, point.attributes): float(point.value) for point in scorecard.metrics}


def test_run_composes_every_existing_report_and_complete_trend(tmp_path: Path) -> None:
    hub = tmp_path / "hub.db"
    trend = tmp_path / "trend.db"
    _seed_hub(hub)
    append_scorecard(trend, _benchmark(100.0, started_at=10.0))
    append_scorecard(trend, _benchmark(125.0, started_at=20.0))
    history = load_history(trend)

    scorecard = run_fleet_scorecard(
        hub,
        benchmark_runs=history,
        pricing={"local-model": ModelPrice(input_per_1k=2.0, output_per_1k=4.0)},
        budgets={"alice": 3.0},
        service_name="hub-test",
    )

    assert scorecard.generated_from_seq == 5
    assert scorecard.as_of == 5.0
    assert scorecard.causality.service_name == "hub-test"
    assert scorecard.accounting.totals.calls == 2
    assert scorecard.accounting.totals.estimated_cost == pytest.approx(4.0)
    assert len(scorecard.conflicts) == 1
    assert scorecard.conflicts[0].yielder.owner == "bob"
    assert scorecard.reliability.findings
    assert scorecard.benchmark_runs is not None
    assert len(scorecard.benchmark_runs) == 2

    points = _points(scorecard)
    assert points[("synapse.fleet.accounting.calls", ())] == 2.0
    assert points[("synapse.fleet.conflicts", ())] == 1.0
    assert points[("synapse.fleet.causality.traces", ())] == 2.0
    change = next(
        value
        for (name, attributes), value in points.items()
        if name == "synapse.fleet.benchmark.relative_change"
        and dict(attributes)["metric"] == "events_per_second"
    )
    assert change == pytest.approx(0.25)

    document = cast("dict[str, Any]", fleet_scorecard_to_json(scorecard))
    assert document["schema_version"] == SCORECARD_SCHEMA_VERSION
    assert document["generated_from_seq"] == 5
    assert document["benchmark_trend"]["runs"][1]["run_id"] == 2
    assert document["benchmark_trend"] == trend_to_json(history)
    assert document["conflicts"][0]["yielder"]["owner"] == "bob"
    assert document["accounting"]["note"].startswith("opt-in usage evidence")
    assert document["reliability"]["note"] == "audit signals, not scores"
    assert document["causality"]["service_name"] == "hub-test"
    assert document["metrics"]
    assert "accounting is opt-in" in document["note"]


def test_no_trend_is_distinct_from_an_explicit_empty_trend_store(tmp_path: Path) -> None:
    hub = tmp_path / "hub.db"
    trend = tmp_path / "trend.db"
    _seed_hub(hub)
    EventStore(trend).close()

    absent = run_fleet_scorecard(hub)
    empty = run_fleet_scorecard(hub, benchmark_runs=load_history(trend))

    assert absent.benchmark_runs is None
    assert empty.benchmark_runs == ()
    assert fleet_scorecard_to_json(absent)["benchmark_trend"] is None
    assert fleet_scorecard_to_json(empty)["benchmark_trend"] == {
        "runs": [],
        "context_breaks": [],
        "note": "host-dependent series; compare within one context segment",
    }
    assert not any(point.name.startswith("synapse.fleet.benchmark") for point in empty.metrics)


def test_relative_change_requires_nonzero_matching_context(tmp_path: Path) -> None:
    hub = tmp_path / "hub.db"
    _seed_hub(hub)
    base = run_fleet_scorecard(hub)
    prior_zero = StoredRun(1, 1.0, "v", "cpu", "performance", {"p": {"m": 0.0}})
    prior_other = StoredRun(2, 2.0, "v", "other", "performance", {"p": {"m": 8.0}})
    latest = StoredRun(3, 3.0, "v", "cpu", "performance", {"p": {"m": 4.0}})

    scorecard = build_fleet_scorecard(
        causality=base.causality,
        accounting=base.accounting,
        conflicts=base.conflicts,
        reliability=base.reliability,
        benchmark_runs=(prior_zero, prior_other, latest),
    )

    benchmark_names = [point.name for point in scorecard.metrics if ".benchmark." in point.name]
    assert benchmark_names == ["synapse.fleet.benchmark.latest"]
    trend = cast("dict[str, Any]", fleet_scorecard_to_json(scorecard))["benchmark_trend"]
    assert trend["context_breaks"] == [
        {"before_run_id": 2, "changes": ["cpu cpu→other"]},
        {"before_run_id": 3, "changes": ["cpu other→cpu"]},
    ]

    no_match = build_fleet_scorecard(
        causality=base.causality,
        accounting=base.accounting,
        conflicts=base.conflicts,
        reliability=base.reliability,
        benchmark_runs=(prior_other, latest),
    )
    assert [point.name for point in no_match.metrics if ".benchmark." in point.name] == [
        "synapse.fleet.benchmark.latest"
    ]


def test_metric_point_json_preserves_sorted_dimensions(tmp_path: Path) -> None:
    hub = tmp_path / "hub.db"
    _seed_hub(hub)
    scorecard = run_fleet_scorecard(hub)
    conflict = next(point for point in scorecard.metrics if point.name.endswith("conflict.present"))

    payload = cast("dict[str, Any]", metric_point_to_json(conflict))

    assert payload["value"] == 1
    assert list(payload["attributes"]) == sorted(payload["attributes"])
    assert payload["unit"] == "1"
    assert "preempted" in payload["description"]


def test_run_refuses_missing_or_over_ceiling_stores(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_fleet_scorecard(tmp_path / "absent.db")

    hub = tmp_path / "hub.db"
    _seed_hub(hub)
    with pytest.raises(ValueError, match="would exceed 1 coordination events"):
        run_fleet_scorecard(hub, max_nodes=1)
