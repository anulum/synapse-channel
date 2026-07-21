# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard activity feed builder regressions

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard_store_feeds_helpers import _seed_log
from synapse_channel.core.journal import (
    EventKind,
    record_ledger_task,
    record_operator_relay,
    record_release,
)
from synapse_channel.core.ledger import LedgerTask
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard_store_feeds import (
    DEFAULT_OPERATOR_ACTIONS_LIMIT,
    DEFAULT_RECEIPTS_LIMIT,
    build_operator_actions_feed,
    build_receipts_feed,
    build_sessions_feed,
    build_waits_feed,
)
from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_NOTE_KIND,
    format_session_metric_note,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


def _seed_session_metric(
    store: EventStore, *, author: str, session: str, ts: float, **metrics: object
) -> None:
    """Append one cumulative ``session_metric`` progress note to the store."""
    base: dict[str, object] = {
        "turns": 2,
        "errors": 0,
        "abstentions": 0,
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.1,
        "total_latency_seconds": 2.0,
        "max_rate_limit_utilisation": None,
        "last_input_tokens": 60,
    }
    base.update(metrics)
    note = format_session_metric_note(SessionMetrics(**base))  # type: ignore[arg-type]
    store.append(
        EventKind.LEDGER_PROGRESS,
        {"kind": SESSION_METRIC_NOTE_KIND, "text": note, "author": author, "task_id": session},
        ts=ts,
    )


def _sessions_by_agent(document: dict[str, object]) -> dict[str, dict[str, object]]:
    sessions = document["sessions"]
    assert isinstance(sessions, list)
    by_agent: dict[str, dict[str, object]] = {}
    for record in sessions:
        assert isinstance(record, dict)
        by_agent[str(record["agent"])] = record
    return by_agent


class TestSessionsFeed:
    def test_reports_each_session_with_seq_for_a_causality_join(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_session_metric(store, author="alpha", session="s1", ts=1.0, input_tokens=100)
        _seed_session_metric(store, author="beta", session="s2", ts=2.0, input_tokens=300)
        store.close()

        document = build_sessions_feed(db)

        # The two notes are the log's first two events, so each record's join
        # anchor is the sequence a cockpit hands to the causality feed.
        by_agent = _sessions_by_agent(document)
        assert set(by_agent) == {"alpha", "beta"}
        assert by_agent["alpha"]["seq"] == 1
        assert by_agent["beta"]["seq"] == 2
        assert by_agent["alpha"]["input_tokens"] == 100
        assert by_agent["alpha"]["total_tokens"] == 120  # 100 in + 20 out
        assert document["generated_from_seq"] == 2

    def test_totals_aggregate_cost_across_sessions(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_session_metric(store, author="a", session="x", ts=1.0, cost_usd=0.10)
        _seed_session_metric(store, author="b", session="y", ts=2.0, cost_usd=0.25)
        store.close()

        totals = build_sessions_feed(db)["totals"]

        assert isinstance(totals, dict)
        assert totals["sessions"] == 2
        assert totals["cost_usd"] == pytest.approx(0.35)

    def test_latest_cumulative_snapshot_per_session_wins(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        # Same (agent, session): the later, higher-seq snapshot supersedes.
        _seed_session_metric(store, author="a", session="s", ts=1.0, turns=2)
        _seed_session_metric(store, author="a", session="s", ts=2.0, turns=7)
        store.close()

        document = build_sessions_feed(db)

        by_agent = _sessions_by_agent(document)
        assert len(by_agent) == 1
        assert by_agent["a"]["turns"] == 7

    def test_log_without_session_notes_is_honest_zeroes(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        _seed_log(db)  # claims and releases, no session_metric notes

        document = build_sessions_feed(db)

        assert document["sessions"] == []
        totals = document["totals"]
        assert isinstance(totals, dict)
        assert totals["sessions"] == 0
        assert totals["cost_usd"] == 0.0
        # The absence is stated, not a fabricated cost.
        assert "opt-in" in str(document["note"])

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_sessions_feed(tmp_path / "absent.db")

    def test_document_is_deterministic_over_a_given_log(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_session_metric(store, author="a", session="s", ts=1.0)
        store.close()
        assert build_sessions_feed(db) == build_sessions_feed(db)


def _seed_task(
    store: EventStore,
    *,
    task_id: str,
    title: str = "task",
    depends_on: tuple[str, ...] = (),
    status: str = "open",
    owner: str = "",
    created_by: str = "amy",
    created_at: float = 1.0,
) -> None:
    """Record one declared ledger task into the durable log."""
    record_ledger_task(
        store,
        LedgerTask(
            task_id=task_id,
            title=title,
            created_at=created_at,
            updated_at=created_at,
            depends_on=depends_on,
            status=status,
            suggested_owner=owner,
            created_by=created_by,
        ),
    )


class TestWaitsFeed:
    def test_lists_a_task_blocked_on_an_unfinished_dependency(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_task(store, task_id="PENDING", status="open")  # a dep, not yet done
        _seed_task(
            store,
            task_id="BLOCKED",
            title="ship it",
            depends_on=("PENDING",),
            owner="amy",
            created_at=5.0,
        )
        store.close()

        document = build_waits_feed(db)

        assert document["present"] is True
        assert document["wait_count"] == 1
        waits = document["waits"]
        assert isinstance(waits, list)
        gate = waits[0]
        assert gate["task_id"] == "BLOCKED"
        assert gate["who"] == "amy"
        assert gate["on_what"] == ["PENDING"]
        assert gate["since"] == 5.0

    def test_a_task_whose_dependencies_are_done_is_not_a_gate(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_task(store, task_id="DONE", status="done")
        _seed_task(store, task_id="OK", depends_on=("DONE",), status="open")
        store.close()

        document = build_waits_feed(db)

        assert document["wait_count"] == 0
        assert document["waits"] == []

    def test_a_terminal_task_is_never_a_gate(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        # A cancelled task with an unmet dependency is not a pending gate — it is
        # not waiting on anything, it is finished.
        _seed_task(store, task_id="MISSING", status="open")
        _seed_task(store, task_id="CANCELLED", depends_on=("MISSING",), status="cancelled")
        store.close()

        document = build_waits_feed(db)

        gates = document["waits"]
        assert isinstance(gates, list)
        assert "CANCELLED" not in [gate["task_id"] for gate in gates]

    def test_who_falls_back_to_the_declarer_without_a_suggested_owner(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_task(store, task_id="DEP", status="open")
        _seed_task(store, task_id="G", depends_on=("DEP",), owner="", created_by="declarer")
        store.close()

        gates = build_waits_feed(db)["waits"]
        assert isinstance(gates, list)
        assert gates[0]["who"] == "declarer"

    def test_a_log_of_unblocked_tasks_reports_no_gates(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_task(store, task_id="DONE", status="done")
        _seed_task(store, task_id="STANDALONE", status="open")  # no dependencies
        _seed_task(store, task_id="SATISFIED", depends_on=("DONE",), status="open")  # dep done
        store.close()

        document = build_waits_feed(db)

        assert document["present"] is True
        assert document["waits"] == []
        assert document["wait_count"] == 0

    def test_a_dependency_on_an_undeclared_task_is_a_gate(self, tmp_path: Path) -> None:
        # A task declared before its prerequisite exists on the board is waiting
        # on an absent dependency — the honest gate, not a silent pass.
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_task(store, task_id="EARLY", depends_on=("NOT-YET-DECLARED",))
        store.close()

        document = build_waits_feed(db)

        assert document["wait_count"] == 1
        gate = document["waits"][0]  # type: ignore[index]
        assert gate["on_what"] == ["NOT-YET-DECLARED"]

    def test_a_blocked_status_task_with_unmet_deps_is_a_gate(self, tmp_path: Path) -> None:
        # "blocked" is a non-terminal planning status; such a task with an unmet
        # dependency is still a pending gate.
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_task(store, task_id="DEP", status="open")
        _seed_task(store, task_id="HELD", depends_on=("DEP",), status="blocked")
        store.close()

        gates = build_waits_feed(db)["waits"]
        assert isinstance(gates, list)
        assert "HELD" in [gate["task_id"] for gate in gates]

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_waits_feed(tmp_path / "absent.db")

    def test_document_is_deterministic_over_a_given_log(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        _seed_task(store, task_id="DEP", status="open")
        _seed_task(store, task_id="G", depends_on=("DEP",))
        store.close()
        assert build_waits_feed(db) == build_waits_feed(db)


class TestOperatorActionsFeed:
    def test_lists_journalled_operator_relay_actions(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        record_release(store, "local-only")
        record_operator_relay(
            store,
            {
                "action": "release",
                "namespace": "TEAM",
                "task_id": "T1",
                "direction": "in",
                "status": "applied",
                "peer": "peer-hub",
                "operator": "ops",
                "approver": "second",
                "origin_hub_id": "origin",
                "reason": "wedged",
                "break_glass": True,
                "previous_owner": "worker",
                "applied": True,
                "detail": "released",
            },
        )
        store.close()

        document = build_operator_actions_feed(db)

        assert document["present"] is True
        assert document["action_count"] == 1
        assert document["log_end_seq"] == 2
        actions = document["actions"]
        assert isinstance(actions, list)
        action = actions[0]
        assert action["seq"] == 2
        assert action["action"] == "release"
        assert action["direction"] == "in"
        assert action["status"] == "applied"
        assert action["peer"] == "peer-hub"
        assert action["operator"] == "ops"
        assert action["approver"] == "second"
        assert action["previous_owner"] == "worker"
        assert action["reason"] == "wedged"
        assert action["break_glass"] is True
        assert "ordinary releases" in str(document["note"])

    def test_reports_outbound_and_pending_actions_with_cursor_and_limit(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        record_operator_relay(
            store,
            {
                "action": "release",
                "namespace": "TEAM",
                "task_id": "T1",
                "direction": "out",
                "agent": "local-operator",
                "operator": "ops",
                "origin_hub_id": "edge",
                "owner_hub_id": "owner",
                "applied": False,
                "detail": "scope_not_granted",
            },
        )
        record_operator_relay(
            store,
            {
                "action": "release",
                "namespace": "TEAM",
                "task_id": "T2",
                "direction": "in",
                "status": "pending",
                "operator": "alice",
                "requester": "alice",
                "origin_hub_id": "edge",
                "applied": False,
                "detail": "awaiting approval",
            },
        )
        store.close()

        document = build_operator_actions_feed(db, since=1, limit=1)

        actions = document["actions"]
        assert isinstance(actions, list)
        assert len(actions) == 1
        assert actions[0]["task_id"] == "T2"
        assert actions[0]["status"] == "pending"
        assert actions[0]["pending"] is True
        assert actions[0]["requester"] == "alice"
        assert document["next_cursor"] == 2

    def test_empty_log_reports_no_actions(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        EventStore(db).close()

        document = build_operator_actions_feed(db)

        assert document["actions"] == []
        assert document["action_count"] == 0
        assert document["next_cursor"] == 0
        assert DEFAULT_OPERATOR_ACTIONS_LIMIT == 50

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_operator_actions_feed(tmp_path / "absent.db")

    def test_document_is_deterministic_over_a_given_log(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        record_operator_relay(store, {"action": "release", "task_id": "T", "reason": None})
        store.close()

        document = build_operator_actions_feed(db)

        assert document == build_operator_actions_feed(db)
        actions = document["actions"]
        assert isinstance(actions, list)
        assert actions[0]["reason"] == ""


class TestReceiptsFeed:
    def test_projects_universal_receipts_from_the_durable_log(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        store.append(EventKind.CHAT, {"sender": "alice", "target": "all"}, ts=1.0)
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "task_id": "REL",
                "author": "owner",
                "kind": "assessment",
                "text": "release receipt: evidence=pytest; epistemic_status=supported",
            },
            ts=2.0,
        )
        record_operator_relay(
            store,
            {"action": "release", "task_id": "REMOTE", "operator": "ops", "applied": True},
        )
        store.close()

        document = build_receipts_feed(db)

        assert document["present"] is True
        assert document["receipt_count"] == 2
        assert document["log_end_seq"] == 3
        receipts = document["receipts"]
        assert isinstance(receipts, list)
        assert [receipt["kind"] for receipt in receipts] == ["claim", "operator-relay"]
        assert receipts[0]["status"] == "supported"
        assert receipts[1]["actor"] == "ops"
        assert "ordinary events" in str(document["note"])

    def test_cursor_and_limit_select_recent_receipts(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        store = EventStore(db)
        for seq in range(3):
            store.append(
                EventKind.SANDBOX_RUN,
                {"tool_id": f"tool-{seq}", "exit": "ok", "fuel_used": seq},
                ts=float(seq),
            )
        store.close()

        document = build_receipts_feed(db, since=1, limit=1)

        receipts = document["receipts"]
        assert isinstance(receipts, list)
        assert [receipt["subject"] for receipt in receipts] == ["tool-2"]
        assert document["next_cursor"] == 3

    def test_empty_log_reports_no_receipts(self, tmp_path: Path) -> None:
        db = tmp_path / "hub.db"
        EventStore(db).close()

        document = build_receipts_feed(db)

        assert document["receipts"] == []
        assert document["receipt_count"] == 0
        assert document["next_cursor"] == 0
        assert DEFAULT_RECEIPTS_LIMIT == 100

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing event store"):
            build_receipts_feed(tmp_path / "absent.db")
