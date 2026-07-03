# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — log-derived signals render as valid node_exporter textfiles

"""The reliability and causal-health textfiles must be valid, labelled, and honest.

Every rendered file is parsed back with the real Prometheus client parser — a
textfile that node_exporter would reject is a silent monitoring gap, so the
test refuses to accept anything the parser refuses. The values are checked
against a seeded log so the projection cannot drift from the report it renders.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.causality_health import run_causal_health
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.reliability import run_reliability_report
from synapse_channel.observability_textfile import (
    render_health_textfile,
    render_reliability_textfile,
)

text_string_to_metric_families = pytest.importorskip(
    "prometheus_client.parser"
).text_string_to_metric_families


def _seed(db: Path) -> None:
    store = EventStore(db)
    # alice claims two tasks and never releases them; T2 also declares a failed check
    store.append(
        EventKind.CLAIM,
        {"task_id": "T1", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=10.0,
    )
    store.append(
        EventKind.CLAIM,
        {"task_id": "T2", "owner": "bob", "status": "claimed", "paths": [], "worktree": "w"},
        ts=20.0,
    )
    store.close()


def _families(text: str) -> dict[str, object]:
    return {family.name: family for family in text_string_to_metric_families(text)}


def _sample(family: object, **labels: str) -> float:
    for sample in family.samples:  # type: ignore[attr-defined]
        if all(sample.labels.get(key) == val for key, val in labels.items()):
            return float(sample.value)
    raise AssertionError(f"no sample matching {labels}")


class TestReliabilityTextfile:
    def test_parses_as_valid_exposition(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed(db)

        text = render_reliability_textfile(run_reliability_report(db))
        families = _families(text)

        assert "synapse_reliability_findings" in families
        assert "synapse_reliability_owner_findings" in families
        assert "synapse_reliability_generated_from_seq" in families

    def test_all_four_kinds_are_present_even_at_zero(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed(db)

        families = _families(render_reliability_textfile(run_reliability_report(db)))
        kinds = {
            sample.labels["kind"]
            for sample in families["synapse_reliability_findings"].samples  # type: ignore[attr-defined]
        }
        assert kinds == {
            "stale_claim",
            "declared_failed_check",
            "broken_handoff",
            "conflict_pair",
        }

    def test_watermark_matches_the_report(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed(db)
        report = run_reliability_report(db)

        families = _families(render_reliability_textfile(report))
        assert (
            _sample(families["synapse_reliability_generated_from_seq"]) == report.generated_from_seq
        )

    def test_render_is_deterministic(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed(db)
        report = run_reliability_report(db)
        assert render_reliability_textfile(report) == render_reliability_textfile(report)


class TestHealthTextfile:
    def test_parses_and_counts_the_orphan(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed(db)  # both claims are their task's last event -> two orphans

        report = run_causal_health(db)
        families = _families(render_health_textfile(report))

        assert _sample(families["synapse_causal_health_anomalies"], shape="orphaned") == len(
            report.orphaned
        )
        assert _sample(families["synapse_causal_health_anomalies_total"]) == report.anomaly_count
        assert _sample(families["synapse_causal_health_tasks_scanned"]) == report.tasks_scanned

    def test_all_three_shapes_are_labelled(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed(db)

        families = _families(render_health_textfile(run_causal_health(db)))
        shapes = {
            sample.labels["shape"]
            for sample in families["synapse_causal_health_anomalies"].samples  # type: ignore[attr-defined]
        }
        assert shapes == {"orphaned", "dangling", "stale"}

    def test_label_values_are_escaped(self) -> None:
        from synapse_channel.observability_textfile import _sample as render_sample

        line = render_sample("m", {"owner": 'a"b\\c'}, 1)
        assert line == 'm{owner="a\\"b\\\\c"} 1'


def test_reliability_render_skips_an_unrecognised_finding_kind() -> None:
    """A finding of a kind outside the fixed four is not counted into any series."""
    from synapse_channel.core.reliability import ReliabilityFinding, ReliabilityReport

    report = ReliabilityReport(
        generated_from_seq=5,
        as_of=100.0,
        findings=(
            ReliabilityFinding(
                kind="some_future_kind",
                owner="alice",
                task_id="T1",
                seq=1,
                ts=10.0,
                detail="",
                evidence={},
            ),
        ),
        owners=(),
    )

    families = _families(render_reliability_textfile(report))
    # every known series is present and zero; the unknown kind added no series
    for sample in families["synapse_reliability_findings"].samples:  # type: ignore[attr-defined]
        assert sample.value == 0
        assert sample.labels["kind"] != "some_future_kind"
