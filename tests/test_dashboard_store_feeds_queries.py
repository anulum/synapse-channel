# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard query-feed builder regressions

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard_store_feeds_helpers import _seed_log
from synapse_channel.core.causality import causality_to_json, run_causality
from synapse_channel.core.journal import (
    EventKind,
    record_claim,
    record_release,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim
from synapse_channel.dashboard_store_feeds import (
    DEFAULT_EVENTS_LIMIT,
    MAX_EVENTS_LIMIT,
    build_causality_feed,
    build_events_tail,
    build_metrics_feed,
    build_state_at_feed,
    latest_cursor,
    resolve_task_last_seq,
)


class TestEventsTail:
    def test_tail_returns_events_past_the_cursor_with_real_seq_and_ts(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_events_tail(db, since=3)

        events = document["events"]
        assert isinstance(events, list)
        assert [event["seq"] for event in events] == [4, 5]
        assert [event["ts"] for event in events] == [4.0, 5.0]
        assert events[0]["kind"] == EventKind.CLAIM
        assert events[0]["payload"]["task_id"] == "X"
        assert document["next_cursor"] == 5
        assert document["log_end_seq"] == 5

    def test_limit_bounds_the_batch_and_cursor_resumes_it(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        first = build_events_tail(db, since=0, limit=2)
        first_events = first["events"]
        assert isinstance(first_events, list)
        assert [event["seq"] for event in first_events] == [1, 2]
        assert first["next_cursor"] == 2
        assert first["log_end_seq"] == 5

        second = build_events_tail(db, since=int(str(first["next_cursor"])), limit=2)
        second_events = second["events"]
        assert isinstance(second_events, list)
        assert [event["seq"] for event in second_events] == [3, 4]
        assert second["log_end_seq"] == 5

    def test_caught_up_tail_is_empty_and_keeps_the_cursor(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_events_tail(db, since=99)

        assert document["events"] == []
        assert document["next_cursor"] == 99
        assert document["log_end_seq"] == 5

    def test_limit_is_clamped_to_the_ceiling_and_floor(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        floored_events = build_events_tail(db, since=0, limit=0)["events"]
        assert isinstance(floored_events, list)
        assert len(floored_events) == 1

        ceiling_events = build_events_tail(db, since=0, limit=MAX_EVENTS_LIMIT * 100)["events"]
        assert isinstance(ceiling_events, list)
        assert len(ceiling_events) == 5

    def test_default_limit_is_the_documented_value(self) -> None:
        assert DEFAULT_EVENTS_LIMIT == 200
        assert MAX_EVENTS_LIMIT == 1000

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_events_tail(tmp_path / "absent.db")


class TestTaskResolver:
    def test_resolves_the_tasks_most_recent_event(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        assert resolve_task_last_seq(db, "A") == 3
        assert resolve_task_last_seq(db, "X") == 5

    def test_unrecorded_task_resolves_to_none(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        assert resolve_task_last_seq(db, "GHOST") is None

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            resolve_task_last_seq(tmp_path / "absent.db", "A")


class TestCausalityFeed:
    def test_seq_anchor_mirrors_the_cli_json_exactly(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_causality_feed(db, direction="causes", seq=3)

        assert document == causality_to_json(run_causality(db, "causes", 3))

    def test_task_anchor_resolves_to_the_last_event(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        by_task = build_causality_feed(db, direction="effects", task="A")
        by_seq = build_causality_feed(db, direction="effects", seq=3)

        assert by_task == by_seq

    def test_unknown_task_is_refused_not_invented(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        with pytest.raises(ValueError, match="no recorded event for task 'GHOST'"):
            build_causality_feed(db, direction="causes", task="GHOST")

    def test_exactly_one_anchor_is_required(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        with pytest.raises(ValueError, match="exactly one of seq and task"):
            build_causality_feed(db, direction="causes")
        with pytest.raises(ValueError, match="exactly one of seq and task"):
            build_causality_feed(db, direction="causes", seq=1, task="A")

    def test_only_causes_and_effects_are_served(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        with pytest.raises(ValueError, match="unknown causality direction"):
            build_causality_feed(db, direction="counterfactual", seq=1)


class TestLatestCursor:
    def test_latest_cursor_is_the_logs_highest_sequence(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        assert latest_cursor(db) == 5

    def test_empty_log_starts_at_zero(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        EventStore(db).close()

        assert latest_cursor(db) == 0

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            latest_cursor(tmp_path / "absent.db")


class TestCausalityAbsenceNotes:
    def test_recorded_but_graphless_event_says_so(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)
        store = EventStore(db)
        store.append("chat", {"sender": "P", "text": "hello"}, ts=6.0)
        store.close()

        document = build_causality_feed(db, direction="causes", seq=6)

        assert document["present"] is False
        assert "outside the coordination causal graph" in str(document["note"])

    def test_truly_absent_sequence_says_so(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_causality_feed(db, direction="causes", seq=999)

        assert document["present"] is False
        assert document["note"] == "no event recorded at this sequence"

    def test_present_answers_carry_no_note(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)

        document = build_causality_feed(db, direction="causes", seq=3)

        assert document["present"] is True
        assert "note" not in document


class TestMetricsFeed:
    def test_counts_totals_kinds_and_windows(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        base = 100000.0
        store.append(EventKind.CHAT, {"m": 1}, ts=base)  # outside both windows
        store.append(EventKind.CLAIM, {"m": 2}, ts=base + 90000.0)  # inside day only
        store.append(EventKind.CHAT, {"m": 3}, ts=base + 176000.0)  # inside hour
        store.append(EventKind.CHAT, {"m": 4}, ts=base + 176400.0)  # last event
        store.close()

        document = build_metrics_feed(db)

        log = document["log"]
        assert isinstance(log, dict)
        assert log["total_events"] == 4
        assert log["max_seq"] == 4
        assert log["first_ts"] == base
        assert log["last_ts"] == base + 176400.0
        assert document["events_by_kind"] == {"chat": 3, "claim": 1}
        windows = document["windows"]
        assert isinstance(windows, dict)
        assert windows["last_hour"] == {"events": 2, "by_kind": {"chat": 2}}
        assert windows["last_day"] == {"events": 3, "by_kind": {"chat": 2, "claim": 1}}
        assert "hub's own /metrics" in str(document["note"])

    def test_empty_store_is_all_zero_not_an_error(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        EventStore(db).close()

        document = build_metrics_feed(db)

        log = document["log"]
        assert isinstance(log, dict)
        assert log == {"total_events": 0, "max_seq": 0, "first_ts": None, "last_ts": None}
        assert document["events_by_kind"] == {}
        windows = document["windows"]
        assert isinstance(windows, dict)
        assert windows["last_hour"] == {"events": 0, "by_kind": {}}

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_metrics_feed(tmp_path / "absent.db")

    def test_document_is_deterministic_over_a_given_log(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        store.append(EventKind.CHAT, {"m": 1}, ts=500.0)
        store.close()

        assert build_metrics_feed(db) == build_metrics_feed(db)


def _replayable_claim(**overrides: object) -> TaskClaim:
    base: dict[str, object] = {
        "task_id": "T1",
        "owner": "alice",
        "note": "",
        "claimed_at": 1000.0,
        "lease_expires_at": 1_000_000_000_000.0,
        "status": "claimed",
        "data_ref": "",
        "worktree": "w",
        "paths": (),
        "epoch": 1,
    }
    base.update(overrides)
    return TaskClaim(**base)  # type: ignore[arg-type]


def _task_ids(document: dict[str, object]) -> list[str]:
    state = document["state"]
    assert isinstance(state, dict)
    claims = state["active_claims"]
    assert isinstance(claims, list)
    task_ids: list[str] = []
    for claim in claims:
        assert isinstance(claim, dict)
        task_ids.append(str(claim["task_id"]))
    return sorted(task_ids)


class TestStateAtFeed:
    def _seed(self, db: Path) -> None:
        store = EventStore(db)
        record_claim(store, _replayable_claim(task_id="T1", owner="alice"))  # seq 1
        record_claim(store, _replayable_claim(task_id="T2", owner="bob"))  # seq 2
        record_release(store, "T1")  # seq 3
        store.close()

    def test_reconstructs_claims_as_of_a_seq(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)

        at1 = build_state_at_feed(db, seq=1)
        at2 = build_state_at_feed(db, seq=2)
        at3 = build_state_at_feed(db, seq=3)

        assert _task_ids(at1) == ["T1"]  # only T1 claimed yet
        assert _task_ids(at2) == ["T1", "T2"]  # both claimed
        assert _task_ids(at3) == ["T2"]  # T1 released

    def test_carries_as_of_and_log_end(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)

        doc = build_state_at_feed(db, seq=2)
        assert doc["as_of_seq"] == 2
        assert doc["log_end_seq"] == 3
        assert "board" in doc
        assert "presence/roster is not journalled" in str(doc["note"])

    def test_seq_is_clamped_into_range(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)

        assert build_state_at_feed(db, seq=999)["as_of_seq"] == 3  # clamped to log end
        assert build_state_at_feed(db, seq=-5)["as_of_seq"] == 0  # clamped to 0
        assert _task_ids(build_state_at_feed(db, seq=0)) == []  # before any event

    def test_is_deterministic(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        self._seed(db)
        assert build_state_at_feed(db, seq=2) == build_state_at_feed(db, seq=2)

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_state_at_feed(tmp_path / "absent.db", seq=1)
