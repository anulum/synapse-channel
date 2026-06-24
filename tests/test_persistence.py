# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the append-only SQLite event store

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.persistence import EventStore, StoredEvent


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


def test_stored_event_is_named_tuple() -> None:
    event = StoredEvent(seq=1, ts=2.0, kind="chat", payload={"x": 1})
    assert event.seq == 1
    assert event.payload["x"] == 1


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
    mode = store._conn.execute("PRAGMA synchronous").fetchone()[0]
    assert mode == 1  # 1 == NORMAL
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
