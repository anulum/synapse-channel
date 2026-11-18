# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — triggers, their step down in one transaction, and the watcher's due list
"""A trigger and its step down are one commit; the watcher finds each due trigger once."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.handlers.operation_triggers import record_trigger
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import AttemptLedger
from synapse_channel.core.operation_attempts import OperationFact
from synapse_channel.core.operation_triggers import (
    CAUSE_ACTOR_REQUEST,
    CAUSE_DEADLINE_UNVERIFIED,
    CAUSE_INCORRECT_ACCEPTANCE,
    CAUSE_STOP_UNSETTLED,
    Trigger,
    above,
    ladder_refusal,
    level_rows,
    trigger_row,
)
from synapse_channel.core.persistence import EventStore

DECLARER = "P/worker"
EXECUTOR = "P/executor"
CHECKER = "Q/checker"
NOW = 1_800_000_000.0
DIGEST = "d" * 64
BOUND = 900.0
SCOPES = ("operation:op-1", "effect:file", "project:P", "installation")


def _declare(ledger: AttemptLedger, operation_id: str, *, deadline: int | None = None) -> None:
    ledger.note_declaration(
        operation_id,
        OperationFact(
            task_id="T1",
            declarer=DECLARER,
            intent_sha256=DIGEST,
            deadline=deadline,
            declared_at=NOW,
            effect="file",
            assurance="cooperative",
        ),
    )


def _raise_all(ledger: AttemptLedger, level: str = "A2", seq: int = 1) -> None:
    for offset, scope in enumerate(SCOPES):
        ledger.note_level(scope, seq + offset, level=level, epoch=1)


def test_the_rows_of_a_trigger_name_it_and_each_scope_it_lowers() -> None:
    lowers = above(
        (("operation:op-1", "A2", 3), ("project:P", "A1", 2), ("installation", "A3", 1)), "A1"
    )
    assert lowers == (("operation:op-1", "A2", 3), ("installation", "A3", 1))
    trigger = Trigger(CAUSE_ACTOR_REQUEST, "project:P", "A1", lowers, 7, "P/worker", {"x": 1})
    assert trigger_row(trigger, NOW) == {
        "scope": "project:P",
        "cause": "actor_request",
        "target": "A1",
        "shown_by": 7,
        "by": "P/worker",
        "lowered": ["operation:op-1", "installation"],
        "x": 1,
        "hub_time": NOW,
    }
    assert level_rows(trigger, 40, NOW) == [
        (
            EventKind.OPERATION_LEVEL,
            {
                "scope": "operation:op-1",
                "level": "A1",
                "from": "A2",
                "cause": "trigger:40",
                "granted_by": "P/worker",
                "epoch": 4,
                "hub_time": NOW,
            },
        ),
        (
            EventKind.OPERATION_LEVEL,
            {
                "scope": "installation",
                "level": "A1",
                "from": "A3",
                "cause": "trigger:40",
                "granted_by": "P/worker",
                "epoch": 2,
                "hub_time": NOW,
            },
        ),
    ]
    assert ladder_refusal("project:P") == (
        "approval gate: an automated approval needs level A2 or above (scope project:P is at A1)"
    )


def test_an_operation_s_trigger_lowers_every_containing_scope_above_a1() -> None:
    ledger = AttemptLedger(None)
    _declare(ledger, "op-1")
    ledger.note_level("project:P", 5, level="A3", epoch=2)
    ledger.note_level("effect:file", 6, level="A2", epoch=1)
    trigger = ledger.operation_trigger("op-1", CAUSE_INCORRECT_ACCEPTANCE, shown_by=9)
    assert trigger == Trigger(
        CAUSE_INCORRECT_ACCEPTANCE,
        "operation:op-1",
        "A1",
        (("effect:file", "A2", 1), ("project:P", "A3", 2)),
        9,
        "",
        {"operation_id": "op-1"},
    )
    assert ledger.actor_trigger("project:P", "A2", DECLARER).lowers == (("project:P", "A3", 2),)
    assert ledger.actor_trigger("project:P", "A3", DECLARER).lowers == ()


def test_the_watcher_finds_an_ended_unverified_operation_past_its_deadline_once() -> None:
    ledger = AttemptLedger(None)
    for name in ("op-1", "op-open", "op-closed", "op-early"):
        _declare(ledger, name, deadline=int(NOW) - 1 if name != "op-early" else int(NOW) + 60)
        ledger.open_attempt(name, f"{name}-a", executor=EXECUTOR, approval_seq=2)
    _declare(ledger, "op-plain")
    for name in ("op-1", "op-closed", "op-early"):
        ledger.note_outcome(name, EXECUTOR, "confirmed_done", seq=3)
    ledger.note_verification("op-closed", CHECKER, effect="observed", result="verified", seq=4)
    _raise_all(ledger)
    due = ledger.due_triggers(NOW, BOUND)
    assert [(item.cause, item.scope) for item in due] == [
        (CAUSE_DEADLINE_UNVERIFIED, "operation:op-1")
    ]
    assert [scope for scope, _level, _epoch in due[0].lowers] == list(SCOPES)
    ledger.note_trigger(CAUSE_DEADLINE_UNVERIFIED, {"operation_id": "op-1"})
    assert ledger.due_triggers(NOW, BOUND) == []
    assert ledger.due_triggers(NOW + 120, BOUND)[0].scope == "operation:op-early"


def test_only_what_passed_after_the_watcher_started_is_due() -> None:
    """A long journal of old deadlines and stop bounds gives no burst at the first tick."""
    ledger = AttemptLedger(None)
    for number in range(40):
        _declare(ledger, f"op-{number}", deadline=int(NOW) - 600 + number)
        ledger.open_attempt(f"op-{number}", f"a{number}", executor=EXECUTOR, approval_seq=2)
        ledger.note_outcome(f"op-{number}", EXECUTOR, "confirmed_done", seq=3)
    ledger.note_stop(
        "project:P", 10, epoch=1, listed=(("op-0", "a0"),), recorded_at=NOW - 2 * BOUND
    )
    assert len(ledger.due_triggers(NOW, BOUND)) == 41
    assert ledger.due_triggers(NOW, BOUND, since=NOW - 1) == []
    late = ledger.due_triggers(NOW, BOUND, since=NOW - 600 + 37.5)
    assert [item.scope for item in late] == ["operation:op-38", "operation:op-39"]


def test_the_watcher_finds_a_stop_unsettled_past_its_bound_once() -> None:
    ledger = AttemptLedger(None)
    _declare(ledger, "op-1")
    ledger.open_attempt("op-1", "a1", executor=EXECUTOR, approval_seq=2)
    ledger.note_level("project:P", 1, level="A2", epoch=1)
    ledger.note_stop("project:P", 10, epoch=1, listed=(("op-1", "a1"),), recorded_at=NOW - BOUND)
    ledger.note_stop("installation", 11, epoch=1, listed=(), recorded_at=NOW - BOUND)
    ledger.note_stop("effect:file", 12, epoch=1, listed=(("op-1", "a1"),), recorded_at=NOW - 10)
    ledger.note_stop("operation:op-1", 13, epoch=1, listed=(("op-1", "a1"),), recorded_at=0.0)
    due = ledger.due_triggers(NOW, BOUND)
    assert due == [
        Trigger(
            CAUSE_STOP_UNSETTLED,
            "project:P",
            "A1",
            (("project:P", "A2", 1),),
            10,
            "",
            {"stop_seq": 10},
        )
    ]
    ledger.note_trigger(CAUSE_STOP_UNSETTLED, {"stop_seq": 10})
    assert ledger.due_triggers(NOW, BOUND) == []
    ledger.note_resume("effect:file", 14)
    assert ledger.due_triggers(NOW + BOUND, BOUND) == []


def test_a_trigger_and_its_step_down_commit_together_and_are_restored(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        ledger = AttemptLedger(store)
        _declare(ledger, "op-1")
        raised = {"scope": "project:P", "level": "A2", "epoch": 1}
        ledger.note_level(
            "project:P", store.append(EventKind.OPERATION_LEVEL, raised), level="A2", epoch=1
        )
        trigger = ledger.operation_trigger("op-1", CAUSE_DEADLINE_UNVERIFIED)
        sequences = record_trigger(store, ledger, trigger, NOW)
        rows = store.read_since(1, kinds=(EventKind.OPERATION_TRIGGER, EventKind.OPERATION_LEVEL))
    finally:
        store.close()
    assert [row.seq for row in rows] == list(sequences)
    assert [row.kind for row in rows] == [EventKind.OPERATION_TRIGGER, EventKind.OPERATION_LEVEL]
    assert rows[1].payload["cause"] == f"trigger:{sequences[0]}"
    assert (rows[1].payload["scope"], rows[1].payload["epoch"]) == ("project:P", 2)
    assert ledger.level_of("project:P") == ("A1", 2)
    assert ledger.window_start("project:P", 0) == sequences[1]
    store = EventStore(path)
    try:
        restored = AttemptLedger(store)
    finally:
        store.close()
    restored.note_trigger("ignored", {"operation_id": "unknown", "stop_seq": 999})
    assert restored.level_of("project:P") == ("A1", 2)
    assert restored.window_start("project:P", 0) == sequences[1]


def test_a_failed_step_down_leaves_no_trigger(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        store._conn.execute(
            "CREATE TRIGGER reject_level BEFORE INSERT ON events "
            "WHEN NEW.kind = 'operation_level' "
            "BEGIN SELECT RAISE(FAIL, 'level write failed'); END"
        )
        store._conn.commit()
        ledger = AttemptLedger(None)
        _declare(ledger, "op-1")
        ledger.note_level("project:P", 1, level="A2", epoch=1)
        trigger = ledger.operation_trigger("op-1", CAUSE_INCORRECT_ACCEPTANCE, shown_by=1)
        with pytest.raises(sqlite3.IntegrityError, match="level write failed"):
            record_trigger(store, ledger, trigger, NOW)
        assert store.count() == 0
    finally:
        store.close()
    assert ledger.level_of("project:P") == ("A2", 1)
    assert ledger.window_start("project:P", 0) == 0


def test_the_actors_of_a_scope_and_the_a1_floor_of_an_intent() -> None:
    ledger = AttemptLedger(None)
    _declare(ledger, "op-1")
    ledger.open_attempt("op-1", "a1", executor=EXECUTOR, approval_seq=2)
    assert ledger.scope_actors("project:P") == {DECLARER, EXECUTOR}
    assert ledger.scope_actors("project:Q") == frozenset()
    assert ledger.automated_floor(DIGEST) == "operation:op-1"
    _raise_all(ledger)
    assert ledger.automated_floor(DIGEST) is None
    ledger.note_level("project:P", 20, level="A1", epoch=2)
    assert ledger.automated_floor(DIGEST) == "project:P"
    assert ledger.automated_floor("e" * 64) is None
    assert ledger.contradicted_at("op-1") == 0
    assert ledger.contradicted_at("op-unknown") == 0
