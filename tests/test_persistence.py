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
from pathlib import Path

import pytest

from synapse_channel.core.persistence import EventStore, StoredEvent


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

    def deny_commit(
        action: int,
        operation: str | None,
        _table: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store._conn.set_authorizer(deny_commit)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        store.append("claim", {"task_id": "rejected"}, durable=True)
    store._conn.set_authorizer(None)

    assert _synchronous_mode(store) == 1
    assert not store._conn.in_transaction
    assert store.count() == 0
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
