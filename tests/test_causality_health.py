# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — causal-graph anomaly assessment regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.causality_health import (
    DEFAULT_STALE_AFTER,
    assess_causal_health,
    health_to_json,
    render_health_markdown,
    run_causal_health,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent


def _claim(
    seq: int,
    ts: float,
    task: str,
    owner: str,
    *,
    status: str = "claimed",
    kind: str = EventKind.CLAIM,
) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=ts,
        kind=kind,
        payload={
            "task_id": task,
            "owner": owner,
            "status": status,
            "paths": ["src/x"],
            "worktree": "w",
        },
    )


def _release(seq: int, ts: float, task: str) -> StoredEvent:
    return StoredEvent(seq=seq, ts=ts, kind=EventKind.RELEASE, payload={"task_id": task})


def _ledger(seq: int, ts: float, task: str, *, deps: tuple[str, ...] = ()) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=ts,
        kind=EventKind.LEDGER_TASK,
        payload={"task_id": task, "title": f"task {task}", "depends_on": list(deps)},
    )


def _healthy_events() -> tuple[StoredEvent, ...]:
    """B claimed, completed, released — a clean lifecycle."""
    return (
        _ledger(1, 1.0, "B"),
        _claim(2, 2.0, "B", "alice"),
        _claim(3, 3.0, "B", "alice", status="done", kind=EventKind.TASK_UPDATE),
        _release(4, 4.0, "B"),
    )


class TestOrphanedClaims:
    def test_claim_with_no_successor_is_orphaned(self) -> None:
        events = (*_healthy_events(), _claim(5, 5.0, "X", "bob"), _release(6, 9000.0, "B"))

        report = assess_causal_health(events)

        assert [item.task_id for item in report.orphaned] == ["X"]
        orphan = report.orphaned[0]
        assert orphan.owner == "bob"
        assert orphan.seq == 5
        assert orphan.age_seconds == pytest.approx(8995.0)

    def test_claim_followed_by_any_lifecycle_event_is_not_orphaned(self) -> None:
        events = (
            _claim(1, 1.0, "X", "bob"),
            _claim(2, 2.0, "X", "bob", status="working", kind=EventKind.TASK_UPDATE),
        )

        report = assess_causal_health(events)

        assert report.orphaned == ()

    def test_clean_lifecycles_raise_no_anomaly(self) -> None:
        # the trailing taskless release belongs to no task and is ignored
        events = (
            *_healthy_events(),
            StoredEvent(seq=5, ts=5.0, kind=EventKind.RELEASE, payload={}),
        )

        report = assess_causal_health(events)

        assert report.anomaly_count == 0
        assert report.tasks_scanned == 1


class TestDanglingDependencies:
    def test_dependency_that_never_completed_is_flagged(self) -> None:
        events = (
            _ledger(1, 1.0, "A", deps=("B", "C")),
            _claim(2, 2.0, "B", "alice"),
            _claim(3, 3.0, "B", "alice", status="done", kind=EventKind.TASK_UPDATE),
        )

        report = assess_causal_health(events)

        # B completed via done status; C never appears at all
        assert [(item.task_id, item.depends_on) for item in report.dangling] == [("A", "C")]
        assert report.dangling[0].declared_seq == 1

    def test_release_counts_as_completion(self) -> None:
        events = (
            _claim(1, 1.0, "B", "alice"),
            _release(2, 2.0, "B"),
            _ledger(3, 3.0, "A", deps=("B",)),
        )

        report = assess_causal_health(events)

        assert report.dangling == ()

    def test_claimed_but_never_completed_dependency_is_dangling(self) -> None:
        events = (
            _claim(1, 1.0, "B", "alice"),
            _claim(2, 2.0, "B", "alice", status="working", kind=EventKind.TASK_UPDATE),
            _ledger(3, 3.0, "A", deps=("B",)),
        )

        report = assess_causal_health(events)

        assert [(item.task_id, item.depends_on) for item in report.dangling] == [("A", "B")]


class TestStaleClaims:
    def test_unreleased_claim_silent_past_the_threshold_is_stale(self) -> None:
        events = (
            _claim(1, 1.0, "X", "bob"),
            _claim(2, 2.0, "X", "bob", status="working", kind=EventKind.TASK_UPDATE),
            _release(3, 9000.0, "OTHER"),
        )

        report = assess_causal_health(events, stale_after=3600.0)

        assert [item.task_id for item in report.stale] == ["X"]
        stale = report.stale[0]
        assert stale.owner == "bob"
        assert stale.last_seq == 2
        assert stale.age_seconds == pytest.approx(8998.0)

    def test_recent_unreleased_claim_is_not_stale(self) -> None:
        events = (
            _claim(1, 1.0, "X", "bob"),
            _claim(2, 2.0, "X", "bob", status="working", kind=EventKind.TASK_UPDATE),
            _release(3, 100.0, "OTHER"),
        )

        report = assess_causal_health(events, stale_after=3600.0)

        assert report.stale == ()

    def test_completed_task_is_never_stale(self) -> None:
        events = (
            _claim(1, 1.0, "X", "bob"),
            _claim(2, 2.0, "X", "bob", status="done", kind=EventKind.TASK_UPDATE),
            _release(3, 9000.0, "OTHER"),
        )

        report = assess_causal_health(events, stale_after=3600.0)

        assert report.stale == ()

    def test_failed_terminal_is_a_reported_outcome_not_a_stale_lease(self) -> None:
        events = (
            _claim(1, 1.0, "X", "bob"),
            _claim(2, 2.0, "X", "bob", status="failed", kind=EventKind.TASK_UPDATE),
            _release(3, 9000.0, "OTHER"),
        )

        report = assess_causal_health(events, stale_after=3600.0)

        assert report.stale == ()

    def test_never_claimed_task_is_not_a_staleness_candidate(self) -> None:
        # a ledger declaration alone is a plan entry, not a held lease
        events = (
            _ledger(1, 1.0, "P"),
            _release(2, 9000.0, "OTHER"),
        )

        report = assess_causal_health(events, stale_after=3600.0)

        assert report.stale == ()

    def test_owner_falls_back_to_the_last_recorded_owner(self) -> None:
        events = (
            _claim(1, 1.0, "X", "bob"),
            StoredEvent(
                seq=2,
                ts=2.0,
                kind=EventKind.TASK_UPDATE,
                payload={"task_id": "X", "owner": "", "status": "working"},
            ),
            _release(3, 9000.0, "OTHER"),
        )

        report = assess_causal_health(events, stale_after=3600.0)

        assert report.stale[0].owner == "bob"


class TestReportShape:
    def test_empty_log_reports_zero_everything(self) -> None:
        report = assess_causal_health(())

        assert report.anomaly_count == 0
        assert report.tasks_scanned == 0
        assert report.log_end_ts == 0.0
        assert report.stale_after == DEFAULT_STALE_AFTER

    def test_json_carries_all_three_signals_and_the_note(self) -> None:
        events = (
            _ledger(1, 1.0, "A", deps=("GHOST",)),
            _claim(2, 2.0, "A", "bob"),
            _claim(3, 3.0, "X", "eve"),
            _release(4, 9000.0, "OTHER"),
        )

        payload = health_to_json(assess_causal_health(events, stale_after=3600.0))

        # A's own last event is also its claim, so A is orphaned AND stale,
        # X is orphaned AND stale, and A's GHOST dependency dangles: 5 signals
        assert payload["anomaly_count"] == 5
        assert payload["note"] == "recorded-event signals, not verdicts"
        dangling = payload["dangling"]
        assert isinstance(dangling, list)
        assert dangling[0] == {"task_id": "A", "depends_on": "GHOST", "declared_seq": 1}
        orphaned = payload["orphaned"]
        assert isinstance(orphaned, list)
        assert {item["task_id"] for item in orphaned} == {"A", "X"}
        stale = payload["stale"]
        assert isinstance(stale, list)
        assert {item["task_id"] for item in stale} == {"A", "X"}

    def test_markdown_names_each_anomaly(self) -> None:
        events = (
            _ledger(1, 1.0, "A", deps=("GHOST",)),
            _claim(2, 2.0, "X", "eve"),
            _release(3, 9000.0, "OTHER"),
        )

        text = render_health_markdown(assess_causal_health(events, stale_after=3600.0))

        assert "# Causal health: 3 anomalies" in text
        assert "task=A depends on GHOST, which never completed" in text
        assert "seq=2 task=X owner=eve silent 8998s" in text
        assert "## Stale claims (unreleased, silent > 3600s)" in text

    def test_markdown_of_a_healthy_log_says_none_everywhere(self) -> None:
        text = render_health_markdown(assess_causal_health(_healthy_events()))

        assert "# Causal health: 0 anomalies" in text
        assert text.count("- none") == 3

    def test_markdown_singular_anomaly_grammar(self) -> None:
        events = (_ledger(1, 1.0, "A", deps=("GHOST",)),)

        text = render_health_markdown(assess_causal_health(events))

        assert "# Causal health: 1 anomaly " in text


class TestRunFromStore:
    def _seed(self, path: Path, events: tuple[StoredEvent, ...]) -> None:
        store = EventStore(path)
        for event in events:
            store.append(event.kind, event.payload, ts=event.ts)
        store.close()

    def test_assesses_a_persisted_store(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db, (*_healthy_events(), _claim(5, 5.0, "X", "bob")))

        report = run_causal_health(db)

        assert [item.task_id for item in report.orphaned] == ["X"]

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            run_causal_health(tmp_path / "absent.db")

    def test_node_ceiling_is_enforced(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db, _healthy_events())

        with pytest.raises(ValueError, match="would exceed 2 coordination events"):
            run_causal_health(db, max_nodes=2)

    def test_zero_lifts_the_node_ceiling(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db, _healthy_events())

        assert run_causal_health(db, max_nodes=0).tasks_scanned == 1

    def test_stale_after_threads_from_the_store_runner(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(
            db,
            (
                _claim(1, 1.0, "X", "bob"),
                _claim(2, 2.0, "X", "bob", status="working", kind=EventKind.TASK_UPDATE),
                _release(3, 10.0, "OTHER"),
            ),
        )

        assert run_causal_health(db, stale_after=5.0).stale != ()
        assert run_causal_health(db, stale_after=100.0).stale == ()
