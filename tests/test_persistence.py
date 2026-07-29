# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the append-only SQLite event store

from __future__ import annotations

import os
import sqlite3
import stat
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.event_row_recovery import CORRUPT_EVENT_KIND, CorruptEventReason
from synapse_channel.core.persistence import DeliveryReceiptTransition, EventStore, StoredEvent


class _PostCommitResetFailure:
    """Connection proxy that fails only after a durable commit has succeeded."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, sql: str, *args: Any) -> Any:
        if sql == "PRAGMA synchronous=NORMAL":
            raise sqlite3.OperationalError("post-commit reset failed")
        return self._connection.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _synchronous_mode(store: EventStore) -> int:
    row = store._conn.execute("PRAGMA synchronous").fetchone()
    return int(row[0])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_event_store_file_is_owner_only(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.close()
    mode = stat.S_IMODE(os.stat(db).st_mode)
    assert mode & 0o077 == 0  # no group/other access to the plaintext event log


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_wal_sidecars_are_owner_only(tmp_path: Path) -> None:
    # WAL mode mirrors the same plaintext chat/findings into the ``-wal``/``-shm``
    # sidecars; born under the process umask they would otherwise be group/other
    # readable while the main file is locked. They must be just as restricted.
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append("chat", {"p": "secret"})  # force a write so both sidecars exist
    present = [p for p in (tmp_path / "events.db-wal", tmp_path / "events.db-shm") if p.exists()]
    assert present, "WAL mode should leave at least one sidecar on disk after a write"
    for sidecar in present:
        mode = stat.S_IMODE(os.stat(sidecar).st_mode)
        assert mode & 0o077 == 0, f"{sidecar.name} is group/other-accessible"
    store.close()


def test_event_store_in_memory_needs_no_chmod() -> None:
    store = EventStore(":memory:")  # the chmod is skipped for the in-memory store
    store.append("chat", {"p": "x"})
    assert store.count() == 1
    store.close()


def test_append_and_read_all_preserves_order(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append("claim", {"task_id": "T1"}, ts=1.0, durable=True)
    store.append("chat", {"payload": "hi"}, ts=2.0)
    events = store.read_all()
    store.close()

    assert [e.kind for e in events] == ["claim", "chat"]
    assert events[0].payload == {"task_id": "T1"}
    assert events[0].ts == 1.0
    assert events[0].seq < events[1].seq


def test_event_store_serializes_cross_thread_appends(tmp_path: Path) -> None:
    """One store safely orders worker-thread writes on its shared connection."""
    store = EventStore(tmp_path / "events.db")
    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(
            pool.map(
                lambda number: store.append(
                    "claim",
                    {"task_id": f"T{number}"},
                    durable=True,
                ),
                range(64),
            )
        )

    events = store.read_all()
    assert len(set(sequences)) == 64
    assert [event.seq for event in events] == list(range(1, 65))
    assert {event.payload["task_id"] for event in events} == {f"T{number}" for number in range(64)}
    store.close()


def test_append_batch_commits_adjacent_rows_with_one_timestamp(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    sequences = store.append_batch(
        (("release", {"task_id": "T1"}), ("operator_relay", {"task_id": "T1"})),
        ts=12.5,
        durable=True,
    )

    events = store.read_all()
    assert sequences == (1, 2)
    assert [event.kind for event in events] == ["release", "operator_relay"]
    assert [event.ts for event in events] == [12.5, 12.5]
    assert _synchronous_mode(store) == 1
    store.close()


def test_append_batch_rolls_back_every_row_when_a_later_insert_fails(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store._conn.execute(
        "CREATE TRIGGER reject_relay BEFORE INSERT ON events "
        "WHEN NEW.kind = 'operator_relay' "
        "BEGIN SELECT RAISE(FAIL, 'forced second insert failure'); END"
    )
    store._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced second insert failure"):
        store.append_batch(
            (("release", {"task_id": "T1"}), ("operator_relay", {"task_id": "T1"})),
            durable=True,
        )

    assert store.count() == 0
    assert not store._conn.in_transaction
    assert _synchronous_mode(store) == 1
    store.close()


def test_durable_append_never_reports_post_commit_cleanup_as_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = EventStore(tmp_path / "events.db")
    monkeypatch.setattr(store, "_conn", _PostCommitResetFailure(store._conn))

    sequence = store.append("claim", {"task_id": "T1"}, durable=True)

    assert sequence == 1
    assert [(event.kind, event.payload) for event in store.read_all()] == [
        ("claim", {"task_id": "T1"})
    ]
    assert "Could not restore SQLite synchronous=NORMAL" in caplog.text
    store.close()


def test_stored_event_is_named_tuple() -> None:
    event = StoredEvent(seq=1, ts=2.0, kind="chat", payload={"x": 1})
    assert event.seq == 1
    assert event.payload["x"] == 1


def test_delivery_receipt_request_transition_and_outbox_are_atomic(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    message_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "BOB", "msg_id": 7, "payload": "hello"},
        requested={"sender": "ALICE", "target": "BOB", "message_id": 7},
    )

    aggregate = store.delivery_receipt_aggregate(message_seq)
    assert aggregate is not None
    assert aggregate.state == "requested"
    assert [event.kind for event in store.read_all()] == [
        "chat",
        "delivery_receipt_requested",
    ]

    notification_id = store.commit_delivery_receipt_transition(
        kind="delivery_receipt_immediate",
        payload={
            "sender": "ALICE",
            "target": "BOB",
            "message_id": 7,
            "message_seq": message_seq,
            "delivered": False,
            "deferred": False,
        },
        notification={"type": "delivery_receipt", "target": "ALICE", "delivered": False},
        state="pending",
    )
    pending = store.pending_delivery_notifications(sender="ALICE")
    assert len(pending) == 1
    assert pending[0].notification_id == notification_id
    assert pending[0].frame["receipt_notification_id"] == notification_id
    updated = store.delivery_receipt_aggregate(message_seq)
    assert updated is not None and updated.state == "pending"

    store.mark_delivery_notification_attempt(notification_id, delivered=False)
    assert store.pending_delivery_notifications(sender="ALICE")[0].attempts == 1
    store.mark_delivery_notification_attempt(notification_id, delivered=True)
    assert store.pending_delivery_notifications(sender="ALICE") == ()
    store.close()


def test_delivery_receipt_request_rolls_back_chat_and_aggregate_together(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")

    def fail_after_aggregate(stage: str) -> None:
        if stage == "after_aggregate_insert":
            raise RuntimeError("kill point")

    with pytest.raises(RuntimeError, match="kill point"):
        store.commit_delivery_receipt_request(
            chat={"sender": "ALICE", "target": "BOB", "msg_id": 1},
            requested={"sender": "ALICE", "target": "BOB", "message_id": 1},
            stage_hook=fail_after_aggregate,
        )

    assert store.read_all() == []
    assert store.delivery_receipt_aggregate(1) is None
    store.close()


@pytest.mark.parametrize(
    "requested",
    (
        {"target": "BOB", "message_id": 1},
        {"sender": "ALICE", "message_id": 1},
        {"sender": "ALICE", "target": "BOB", "message_id": True},
        {"sender": "ALICE", "target": "BOB", "message_id": "1"},
    ),
)
def test_delivery_receipt_request_rejects_incomplete_identity(
    tmp_path: Path, requested: dict[str, Any]
) -> None:
    store = EventStore(tmp_path / "events.db")
    with pytest.raises(ValueError, match="requires sender, target"):
        store.commit_delivery_receipt_request(chat={"payload": "x"}, requested=requested)
    assert store.read_all() == []
    store.close()


def test_delivery_receipt_request_postcommit_hook_cannot_roll_back_fact(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")

    def fail_after_commit(stage: str) -> None:
        if stage == "after_commit":
            raise RuntimeError("observer failed")

    with pytest.raises(RuntimeError, match="observer failed"):
        store.commit_delivery_receipt_request(
            chat={"sender": "ALICE", "target": "BOB", "msg_id": 1},
            requested={"sender": "ALICE", "target": "BOB", "message_id": 1},
            stage_hook=fail_after_commit,
        )
    assert store.delivery_receipt_aggregate(1) is not None
    assert len(store.read_all()) == 2
    store.close()


def test_delivery_receipt_transition_rolls_back_event_state_and_outbox(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    message_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "BOB", "msg_id": 1},
        requested={"sender": "ALICE", "target": "BOB", "message_id": 1},
    )

    def fail_after_outbox(stage: str) -> None:
        if stage == "after_outbox_insert":
            raise RuntimeError("kill point")

    with pytest.raises(RuntimeError, match="kill point"):
        store.commit_delivery_receipt_transition(
            kind="delivery_receipt_immediate",
            payload={
                "sender": "ALICE",
                "target": "BOB",
                "message_id": 1,
                "message_seq": message_seq,
                "delivered": False,
            },
            notification={"type": "delivery_receipt", "target": "ALICE"},
            state="pending",
            stage_hook=fail_after_outbox,
        )

    aggregate = store.delivery_receipt_aggregate(message_seq)
    assert aggregate is not None and aggregate.state == "requested"
    assert store.pending_delivery_notifications() == ()
    assert [event.kind for event in store.read_all()] == [
        "chat",
        "delivery_receipt_requested",
    ]
    store.close()


def test_delivery_receipt_transition_postcommit_hook_cannot_roll_back_fact(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "events.db")
    message_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "BOB", "msg_id": 1},
        requested={"sender": "ALICE", "target": "BOB", "message_id": 1},
    )

    def fail_after_commit(stage: str) -> None:
        if stage == "after_commit":
            raise RuntimeError("observer failed")

    with pytest.raises(RuntimeError, match="observer failed"):
        store.commit_delivery_receipt_transition(
            kind="delivery_receipt_immediate",
            payload={
                "sender": "ALICE",
                "target": "BOB",
                "message_id": 1,
                "message_seq": message_seq,
                "delivered": False,
            },
            notification={"target": "ALICE"},
            state="pending",
            stage_hook=fail_after_commit,
        )
    aggregate = store.delivery_receipt_aggregate(message_seq)
    assert aggregate is not None and aggregate.state == "pending"
    assert len(store.pending_delivery_notifications()) == 1
    store.close()


def test_delivery_receipt_pending_and_bounded_expiry_commit_together(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    old_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "BOB", "msg_id": 1},
        requested={"sender": "ALICE", "target": "BOB", "message_id": 1},
    )
    store.commit_delivery_receipt_transition(
        kind="delivery_receipt_immediate",
        payload={
            "sender": "ALICE",
            "target": "BOB",
            "message_id": 1,
            "message_seq": old_seq,
            "delivered": False,
        },
        notification={"type": "delivery_receipt", "target": "ALICE"},
        state="pending",
    )
    new_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "CAROL", "msg_id": 2},
        requested={"sender": "ALICE", "target": "CAROL", "message_id": 2},
    )

    store.commit_delivery_receipt_transition(
        kind="delivery_receipt_immediate",
        payload={
            "sender": "ALICE",
            "target": "CAROL",
            "message_id": 2,
            "message_seq": new_seq,
            "delivered": False,
        },
        notification={"type": "delivery_receipt", "target": "ALICE"},
        state="pending",
        companion=DeliveryReceiptTransition(
            "delivery_receipt_expired",
            {
                "sender": "ALICE",
                "target": "BOB",
                "message_id": 1,
                "message_seq": old_seq,
                "delivered": False,
                "deferred": True,
            },
            {"type": "delivery_receipt", "target": "ALICE", "expired": True},
            "expired",
        ),
    )

    old = store.delivery_receipt_aggregate(old_seq)
    new = store.delivery_receipt_aggregate(new_seq)
    assert old is not None and old.state == "expired"
    assert new is not None and new.state == "pending"
    assert {item.notification_id for item in store.pending_delivery_notifications()} == {
        f"delivery-receipt:{old_seq}:pending",
        f"delivery-receipt:{old_seq}:expired",
        f"delivery-receipt:{new_seq}:pending",
    }
    store.close()


def test_delivery_receipt_companion_rolls_back_with_primary(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    old_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "BOB", "msg_id": 1},
        requested={"sender": "ALICE", "target": "BOB", "message_id": 1},
    )
    store.commit_delivery_receipt_transition(
        kind="delivery_receipt_immediate",
        payload={
            "sender": "ALICE",
            "target": "BOB",
            "message_id": 1,
            "message_seq": old_seq,
            "delivered": False,
        },
        notification={"type": "delivery_receipt", "target": "ALICE"},
        state="pending",
    )
    for pending in store.pending_delivery_notifications():
        store.mark_delivery_notification_attempt(pending.notification_id, delivered=True)
    new_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "CAROL", "msg_id": 2},
        requested={"sender": "ALICE", "target": "CAROL", "message_id": 2},
    )

    def fail_after_outbox(stage: str) -> None:
        if stage == "after_outbox_insert":
            raise RuntimeError("companion kill point")

    with pytest.raises(RuntimeError, match="companion kill point"):
        store.commit_delivery_receipt_transition(
            kind="delivery_receipt_immediate",
            payload={
                "sender": "ALICE",
                "target": "CAROL",
                "message_id": 2,
                "message_seq": new_seq,
                "delivered": False,
            },
            notification={"type": "delivery_receipt", "target": "ALICE"},
            state="pending",
            companion=DeliveryReceiptTransition(
                "delivery_receipt_expired",
                {
                    "sender": "ALICE",
                    "target": "BOB",
                    "message_id": 1,
                    "message_seq": old_seq,
                    "delivered": False,
                },
                {"type": "delivery_receipt", "target": "ALICE"},
                "expired",
            ),
            stage_hook=fail_after_outbox,
        )

    old = store.delivery_receipt_aggregate(old_seq)
    new = store.delivery_receipt_aggregate(new_seq)
    assert old is not None and old.state == "pending"
    assert new is not None and new.state == "requested"
    assert store.pending_delivery_notifications() == ()
    store.close()


def test_delivery_receipt_transition_rejects_invalid_inputs_and_state(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    message_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "BOB", "msg_id": 1},
        requested={"sender": "ALICE", "target": "BOB", "message_id": 1},
    )

    base = {
        "sender": "ALICE",
        "target": "BOB",
        "message_id": 1,
        "message_seq": message_seq,
        "delivered": False,
    }
    invalid_cases = (
        ({**base, "message_seq": 0}, "delivery_receipt_immediate"),
        ({**base, "delivered": 0}, "delivery_receipt_immediate"),
        (base, "delivery_receipt_deferred"),
    )
    for payload, kind in invalid_cases:
        with pytest.raises(ValueError):
            store.commit_delivery_receipt_transition(
                kind=kind,
                payload=payload,
                notification={"target": "ALICE"},
                state="pending",
            )

    with pytest.raises(KeyError, match="aggregate is absent"):
        store.commit_delivery_receipt_transition(
            kind="delivery_receipt_immediate",
            payload={**base, "message_seq": 999},
            notification={"target": "ALICE"},
            state="pending",
        )
    with pytest.raises(ValueError, match="does not match"):
        store.commit_delivery_receipt_transition(
            kind="delivery_receipt_immediate",
            payload={**base, "target": "MALLORY"},
            notification={"target": "ALICE"},
            state="pending",
        )
    store.commit_delivery_receipt_transition(
        kind="delivery_receipt_immediate",
        payload=base,
        notification={"target": "ALICE"},
        state="pending",
    )
    with pytest.raises(ValueError, match="invalid from current state"):
        store.commit_delivery_receipt_transition(
            kind="delivery_receipt_immediate",
            payload=base,
            notification={"target": "ALICE"},
            state="pending",
        )
    store.close()


def test_delivery_notification_query_and_settlement_reject_invalid_rows(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    with pytest.raises(ValueError, match="limit"):
        store.pending_delivery_notifications(limit=0)
    with pytest.raises(ValueError, match="identity"):
        store.mark_delivery_notification_attempt("", delivered=False)
    with pytest.raises(KeyError, match="absent"):
        store.mark_delivery_notification_attempt("missing", delivered=False)

    message_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "BOB", "msg_id": 1},
        requested={"sender": "ALICE", "target": "BOB", "message_id": 1},
    )
    notification_id = store.commit_delivery_receipt_transition(
        kind="delivery_receipt_immediate",
        payload={
            "sender": "ALICE",
            "target": "BOB",
            "message_id": 1,
            "message_seq": message_seq,
            "delivered": True,
        },
        notification={"target": "ALICE"},
        state="immediate_delivered",
    )
    store._conn.execute(
        "UPDATE delivery_receipt_outbox SET frame_json = '[]' WHERE notification_id = ?",
        (notification_id,),
    )
    store._conn.commit()
    with pytest.raises(ValueError, match="JSON object"):
        store.pending_delivery_notifications()
    store._conn.execute(
        "UPDATE delivery_receipt_outbox SET frame_json = '{}' WHERE notification_id = ?",
        (notification_id,),
    )
    store._conn.commit()
    store.mark_delivery_notification_attempt(notification_id, delivered=True)
    with pytest.raises(KeyError, match="settled"):
        store.mark_delivery_notification_attempt(notification_id, delivered=True)
    store.close()


def test_delivery_receipt_cleanup_failure_preserves_committed_outbox_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = EventStore(tmp_path / "events.db")
    message_seq = store.commit_delivery_receipt_request(
        chat={"sender": "ALICE", "target": "BOB", "msg_id": 1},
        requested={"sender": "ALICE", "target": "BOB", "message_id": 1},
    )
    notification_id = store.commit_delivery_receipt_transition(
        kind="delivery_receipt_immediate",
        payload={
            "sender": "ALICE",
            "target": "BOB",
            "message_id": 1,
            "message_seq": message_seq,
            "delivered": False,
        },
        notification={"target": "ALICE"},
        state="pending",
    )
    monkeypatch.setattr(store, "_conn", _PostCommitResetFailure(store._conn))
    store.mark_delivery_notification_attempt(notification_id, delivered=False)
    assert store.pending_delivery_notifications()[0].attempts == 1
    assert "Could not restore SQLite synchronous=NORMAL" in caplog.text
    store.close()


def test_restore_normal_failure_before_commit_propagates(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store._conn = _PostCommitResetFailure(store._conn)
    with pytest.raises(sqlite3.OperationalError, match="post-commit reset failed"):
        store._restore_normal_after_transaction(False)
    store.close()


def test_legacy_receipt_events_backfill_the_additive_aggregate(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    message_seq = store.append(
        "chat", {"sender": "ALICE", "target": "BOB", "msg_id": 4, "payload": "old"}
    )
    store.append(
        "delivery_receipt_requested",
        {
            "sender": "ALICE",
            "target": "BOB",
            "message_id": 4,
            "message_seq": message_seq,
        },
    )
    store.append(
        "delivery_receipt_immediate",
        {
            "sender": "ALICE",
            "target": "BOB",
            "message_id": 4,
            "message_seq": message_seq,
            "delivered": False,
        },
    )
    store.close()

    reopened = EventStore(db)
    aggregate = reopened.delivery_receipt_aggregate(message_seq)
    assert aggregate is not None
    assert aggregate.state == "pending"
    assert aggregate.delivered is False
    assert reopened.pending_delivery_notifications() == ()
    reopened.close()


def test_legacy_receipt_backfill_skips_malformed_and_projects_final_states(
    tmp_path: Path,
) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store._conn.executemany(
        "INSERT INTO events (ts, kind, payload) VALUES (1, ?, ?)",
        (
            ("delivery_receipt_requested", "{"),
            ("delivery_receipt_requested", "[]"),
            ("delivery_receipt_requested", "{}"),
        ),
    )
    message_seq = store.append(
        "chat", {"sender": "ALICE", "target": "BOB", "msg_id": 9, "payload": "old"}
    )
    common = {
        "sender": "ALICE",
        "target": "BOB",
        "message_id": 9,
        "message_seq": message_seq,
    }
    store.append("delivery_receipt_requested", common)
    store.append("delivery_receipt_immediate", {**common, "delivered": True})
    store.append("delivery_receipt_deferred", {**common, "delivered": True, "deferred": True})
    store.append("delivery_receipt_expired", {**common, "delivered": False, "deferred": True})
    store.close()

    reopened = EventStore(db)
    aggregate = reopened.delivery_receipt_aggregate(message_seq)
    assert aggregate is not None and aggregate.state == "expired"
    assert aggregate.delivered is False
    reopened.close()


def test_count_tracks_appends(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    assert store.count() == 0
    store.append("chat", {"p": "a"})
    store.append("chat", {"p": "b"})
    assert store.count() == 2
    store.close()


def test_durable_and_normal_writes_both_persist(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append("claim", {"id": "T1"}, durable=True)
    store.append("chat", {"p": "x"}, durable=False)
    assert store.count() == 2
    # Connection is restored to NORMAL after a durable write.
    assert _synchronous_mode(store) == 1  # 1 == NORMAL
    store.close()


def test_durable_append_restores_normal_after_insert_failure(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store._conn.execute(
        "CREATE TRIGGER reject_event BEFORE INSERT ON events "
        "BEGIN SELECT RAISE(FAIL, 'forced insert failure'); END"
    )
    store._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced insert failure"):
        store.append("claim", {"task_id": "rejected"}, durable=True)

    assert _synchronous_mode(store) == 1
    assert not store._conn.in_transaction
    assert store.count() == 0
    store._conn.execute("DROP TRIGGER reject_event")
    store._conn.commit()
    assert store.append("claim", {"task_id": "accepted"}) == 1
    store.close()


def test_durable_append_restores_normal_after_commit_failure(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store._conn.execute("PRAGMA foreign_keys=ON")
    store._conn.execute("CREATE TABLE commit_guard (id INTEGER PRIMARY KEY)")
    store._conn.execute(
        "CREATE TABLE deferred_event_guard ("
        "event_seq INTEGER NOT NULL, parent_id INTEGER NOT NULL, "
        "FOREIGN KEY (parent_id) REFERENCES commit_guard(id) "
        "DEFERRABLE INITIALLY DEFERRED)"
    )
    store._conn.execute(
        "CREATE TRIGGER reject_event_commit AFTER INSERT ON events "
        "BEGIN INSERT INTO deferred_event_guard (event_seq, parent_id) "
        "VALUES (NEW.seq, 1); END"
    )
    store._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        store.append("claim", {"task_id": "rejected"}, durable=True)

    assert _synchronous_mode(store) == 1
    assert not store._conn.in_transaction
    assert store.count() == 0
    store._conn.execute("DROP TRIGGER reject_event_commit")
    store._conn.commit()
    assert store.append("claim", {"task_id": "accepted"}) == 1
    store.close()


def test_uses_wal_journal_mode(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    store.close()


def test_data_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    first = EventStore(db)
    first.append("claim", {"task_id": "T1"}, ts=1.0, durable=True)
    first.close()

    reopened = EventStore(db)
    events = reopened.read_all()
    reopened.close()
    assert [e.payload["task_id"] for e in events] == ["T1"]


def test_default_timestamp_uses_clock(tmp_path: Path) -> None:
    before = __import__("time").time()
    store = EventStore(tmp_path / "events.db")
    store.append("chat", {"p": "x"})
    event = store.read_all()[0]
    store.close()
    assert event.ts >= before


def _seeded(tmp_path: Path) -> EventStore:
    store = EventStore(tmp_path / "events.db")
    store.append("claim", {"id": "T1"}, ts=1.0)
    store.append("finding", {"statement": "a"}, ts=2.0)
    store.append("chat", {"p": "x"}, ts=3.0)
    store.append("recall", {"query_text": "q"}, ts=4.0)
    store.append("finding", {"statement": "b"}, ts=5.0)
    return store


def test_read_since_returns_only_events_above_the_cursor(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    all_events = store.read_all()
    after = store.read_since(all_events[1].seq)  # everything after the 2nd event
    store.close()
    assert [e.kind for e in after] == ["chat", "recall", "finding"]
    assert all(e.seq > all_events[1].seq for e in after)


def test_read_since_zero_returns_the_whole_log(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    assert len(store.read_since(0)) == store.count()
    store.close()


def test_read_since_filters_by_kind(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    findings = store.read_since(0, kinds={"finding", "recall"})
    store.close()
    assert [e.kind for e in findings] == ["finding", "recall", "finding"]


def test_read_since_empty_kinds_returns_nothing(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    assert store.read_since(0, kinds=()) == []
    store.close()


def test_read_since_honours_limit_for_batched_walking(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    batch = store.read_since(0, limit=2)
    assert len(batch) == 2
    nxt = store.read_since(batch[-1].seq, limit=2)
    store.close()
    assert [e.seq for e in nxt] == [batch[-1].seq + 1, batch[-1].seq + 2]


def test_read_since_at_the_tail_returns_empty(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    tail = store.read_all()[-1].seq
    assert store.read_since(tail) == []
    store.close()


def test_context_manager_closes_connection(tmp_path: Path) -> None:
    with EventStore(tmp_path / "events.db") as store:
        store.append("chat", {"p": "x"})
        assert store.count() == 1
    # After exit the connection is closed and rejects further use.
    with pytest.raises(sqlite3.ProgrammingError):
        store.count()


def test_max_seq_is_zero_on_an_empty_log(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    assert store.max_seq() == 0
    store.close()


def test_max_seq_tracks_the_highest_sequence(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    tail = store.read_all()[-1].seq
    assert store.max_seq() == tail
    store.close()


def test_latest_at_or_before_handles_empty_prefixes_and_retention_gaps(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    events = store.read_all()
    assert store.latest_at_or_before(0) is None
    assert store.latest_at_or_before(events[2].seq) == events[2]
    store.delete([events[2].seq])
    assert store.latest_at_or_before(events[2].seq) == events[1]
    assert store.latest_at_or_before(10_000) == events[-1]
    store.close()


def test_delete_removes_named_sequences_and_returns_the_count(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    events = store.read_all()
    removed = store.delete([events[0].seq, events[2].seq])
    remaining = [e.kind for e in store.read_all()]
    store.close()
    assert removed == 2
    assert remaining == ["finding", "recall", "finding"]


def test_delete_of_nothing_is_a_no_op(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    assert store.delete([]) == 0
    assert store.count() == 5
    store.close()


def test_delete_counts_only_distinct_existing_sequences_from_an_iterable(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    events = store.read_all()
    doomed = events[1].seq

    removed = store.delete(seq for seq in (doomed, doomed, events[-1].seq + 100))

    assert removed == 1
    assert [event.seq for event in store.read_all()] == [
        event.seq for event in events if event.seq != doomed
    ]
    store.close()


def test_delete_does_not_recycle_sequence_numbers(tmp_path: Path) -> None:
    # The AUTOINCREMENT key must keep climbing past a deleted seq, so a downstream
    # read_since cursor walks the gap instead of re-reading a recycled sequence.
    store = EventStore(tmp_path / "events.db")
    store.append("chat", {"p": "a"})
    store.append("chat", {"p": "b"})
    tail = store.read_all()[-1].seq
    store.delete([tail])
    store.append("chat", {"p": "c"})
    new_seq = store.read_all()[-1].seq
    store.close()
    assert new_seq > tail  # the freed sequence was not reused


def test_delete_only_named_sequences_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append("finding", {"statement": "keep"}, ts=1.0)
    store.append("finding", {"statement": "drop"}, ts=2.0)
    doomed = store.read_all()[-1].seq
    store.delete([doomed])
    store.close()

    reopened = EventStore(db)
    survivors = [e.payload["statement"] for e in reopened.read_all()]
    reopened.close()
    assert survivors == ["keep"]


def test_vacuum_keeps_the_surviving_rows_intact(tmp_path: Path) -> None:
    store = _seeded(tmp_path)
    events = store.read_all()
    store.delete([events[0].seq])
    store.vacuum()
    survivors = [e.kind for e in store.read_all()]
    # Vacuum reclaims free pages without disturbing the rows that remain.
    assert survivors == ["finding", "chat", "recall", "finding"]
    assert store.count() == 4
    # The connection is usable after the rewrite (writes still commit).
    store.append("chat", {"p": "after"})
    assert store.count() == 5
    store.close()


def test_corrupt_json_row_does_not_block_any_read_api(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append("chat", {"payload": "before"}, ts=1.0)
    corrupt_seq = store.append("claim", {"task_id": "T1"}, ts=2.0)
    store.append("chat", {"payload": "after"}, ts=3.0)
    store._conn.execute("UPDATE events SET payload = ? WHERE seq = ?", ("{", corrupt_seq))
    store._conn.commit()

    for events in (
        store.read_all(),
        store.read_since(0),
        store.read_window(min_seq=1, max_seq=3),
        list(store.iter_events()),
    ):
        assert [event.kind for event in events] == ["chat", CORRUPT_EVENT_KIND, "chat"]
        assert events[1].seq == corrupt_seq
        assert events[1].payload["reasons"] == [CorruptEventReason.INVALID_JSON.value]
    store.close()


def test_corrupt_blob_is_not_exposed_and_kind_filter_still_advances(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    corrupt_seq = store.append("finding", {"statement": "classified-secret"}, ts=1.0)
    store._conn.execute(
        "UPDATE events SET payload = ? WHERE seq = ?",
        (sqlite3.Binary(b"classified-secret\xff"), corrupt_seq),
    )
    store._conn.commit()

    (event,) = store.read_since(0, kinds=("finding",))
    assert event.kind == CORRUPT_EVENT_KIND
    assert event.seq == corrupt_seq
    assert event.payload["reasons"] == [CorruptEventReason.INVALID_UTF8.value]
    assert "classified-secret" not in str(event.payload)
    store.close()


def test_corrupt_rows_reports_all_reasons_with_an_inclusive_ceiling(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    first = store.append("claim", {"task_id": "T1"})
    second = store.append("chat", {"payload": "x"})
    store._conn.execute("UPDATE events SET payload = '[]' WHERE seq = ?", (first,))
    store._conn.execute("UPDATE events SET ts = 'bad', kind = '' WHERE seq = ?", (second,))
    store._conn.commit()

    first_only = store.corrupt_rows(through_seq=first)
    all_rows = store.corrupt_rows()
    assert [row.seq for row in first_only] == [first]
    assert [row.seq for row in all_rows] == [first, second]
    assert all_rows[0].reasons == (CorruptEventReason.PAYLOAD_NOT_OBJECT,)
    assert all_rows[1].reasons == (
        CorruptEventReason.INVALID_TIMESTAMP,
        CorruptEventReason.INVALID_KIND,
    )
    store.close()


def test_corrupt_row_recovery_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    seq = store.append("claim", {"task_id": "T1"})
    store._conn.execute("UPDATE events SET payload = 'null' WHERE seq = ?", (seq,))
    store._conn.commit()
    store.close()

    reopened = EventStore(db)
    (event,) = reopened.read_all()
    assert event.kind == CORRUPT_EVENT_KIND
    assert event.seq == seq
    assert reopened.corrupt_rows()[0].seq == seq
    reopened.close()


def test_iter_events_streams_in_sequence_order(tmp_path: Path) -> None:
    """``iter_events`` is a lazy cursor: no list is built and order is by seq."""
    store = EventStore(tmp_path / "events.db")
    for i in range(1, 6):
        store.append("chat", {"n": i}, ts=float(i))
    iterator = store.iter_events()
    assert isinstance(iterator, types.GeneratorType)
    seqs = [event.seq for event in iterator]
    assert seqs == sorted(seqs) and len(seqs) == 5
    store.close()


def test_iter_events_honours_the_inclusive_ceiling(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    for i in range(1, 8):
        store.append("chat", {"n": i}, ts=float(i))
    limited = list(store.iter_events(through_seq=4))
    assert [event.seq for event in limited] == [1, 2, 3, 4]
    assert list(store.iter_events(through_seq=0)) == []
    store.close()


def test_iter_events_round_trips_payload_fields(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append("claim", {"task_id": "T1", "paths": ["a.py"]}, ts=12.5)
    (event,) = list(store.iter_events())
    assert (event.kind, event.ts) == ("claim", 12.5)
    assert event.payload == {"task_id": "T1", "paths": ["a.py"]}
    store.close()


def test_read_all_matches_iter_events(tmp_path: Path) -> None:
    """``read_all`` is now the materialised view of the same streaming read."""
    store = EventStore(tmp_path / "events.db")
    for i in range(1, 4):
        store.append("chat", {"n": i}, ts=float(i))
    assert store.read_all() == list(store.iter_events())
    store.close()


def test_iter_events_filters_kinds_inside_sqlite(tmp_path: Path) -> None:
    """A kind filter keeps uninterested kinds out of the Python stream entirely."""
    store = EventStore(tmp_path / "events.db")
    store.append("chat", {"n": 1}, ts=1.0)
    store.append("claim", {"task_id": "T1"}, ts=2.0)
    store.append("chat", {"n": 2}, ts=3.0)
    store.append("release", {"task_id": "T1"}, ts=4.0)
    kinds = [event.kind for event in store.iter_events(kinds=("claim", "release"))]
    assert kinds == ["claim", "release"]
    assert list(store.iter_events(kinds=())) == []  # empty filter yields nothing
    store.close()


def test_iter_events_combines_kind_filter_with_the_ceiling(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append("claim", {"task_id": "T1"}, ts=1.0)
    store.append("chat", {"n": 1}, ts=2.0)
    store.append("claim", {"task_id": "T2"}, ts=3.0)
    events = list(store.iter_events(through_seq=2, kinds=("claim",)))
    assert [(event.seq, event.kind) for event in events] == [(1, "claim")]
    store.close()


def test_append_returns_the_monotonic_seq_it_assigned(tmp_path: Path) -> None:
    # The returned seq is the durable cursor a reconnecting client resumes from, so
    # it must match the row's persisted sequence and strictly increase per append.
    store = EventStore(tmp_path / "events.db")
    first = store.append("chat", {"p": "a"})
    second = store.append("chat", {"p": "b"})
    events = store.read_all()
    store.close()
    assert second == first + 1
    assert [event.seq for event in events] == [first, second]
