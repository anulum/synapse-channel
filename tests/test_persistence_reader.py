# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — read-only event log open tests
"""The read-only open reads a real event log and never creates, migrates or writes it."""

from __future__ import annotations

import hashlib
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from synapse_channel.core import event_query
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_reader import EventLogReader
from synapse_channel.core.persistence_sqlcipher import (
    ReadOnlyOpenRefused,
    SqlCipherKeyError,
    SqlCipherUnavailableError,
    sqlcipher_available,
)
from synapse_channel.core.persistence_store_format import STORE_FORMAT, StoreFormatRefused


def _snapshot(path: Path) -> tuple[str, list[str]]:
    """Digest the store file and list its directory, the evidence that nothing changed."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest, sorted(entry.name for entry in path.parent.iterdir())


def _assert_read_left_only_wal_sidecars(path: Path, before: tuple[str, list[str]]) -> None:
    """The store's bytes are unchanged; SQLite's empty WAL and its index may be added.

    A read-only reader of a journal that no process has open leaves SQLite's empty
    ``-wal`` and its ``-shm`` index, with the store's own permissions; the next
    writer uses and removes them.
    """
    digest, names = before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    added = sorted(set(entry.name for entry in path.parent.iterdir()) - set(names))
    assert added == [f"{path.name}-shm", f"{path.name}-wal"]
    assert (path.parent / f"{path.name}-wal").stat().st_size == 0
    mode = stat.S_IMODE(path.stat().st_mode)
    assert {stat.S_IMODE((path.parent / name).stat().st_mode) for name in added} == {mode}


def _journal(path: Path, *, key_file: Path | None = None) -> None:
    """Write a small real journal through the hub's own writer, then close it."""
    with EventStore(path, key_file=key_file) as store:
        for index in range(3):
            store.append("chat", {"sender": "PROJ/alice", "text": f"message {index}"})
        store.append("finding", {"agent": "PROJ/alice", "text": "Finding: kept", "ü": "ä"})


def _rewrite(path: Path, *statements: str) -> None:
    """Apply what another release would have written, then close the connection."""
    with closing(sqlite3.connect(path)) as connection:
        for statement in statements:
            connection.execute(statement)
        connection.commit()


def _reads(store: EventStore | EventLogReader) -> dict[str, object]:
    """Call every read the reader offers, the same way on the writer and on the reader."""
    with store.stream_events(kinds=["chat"], after_seq=1) as rows:
        streamed = list(rows)
    return {
        "all": store.read_all(),
        "since": store.read_since(1),
        "since_kinds": store.read_since(0, kinds=["finding"], limit=5),
        "latest": store.read_latest(limit=2),
        "window": store.read_window(min_seq=2, max_seq=3),
        "iter": list(store.iter_events(through_seq=2)),
        "streamed": streamed,
        "count": store.count(),
        "max_seq": store.max_seq(),
        "at_or_before": store.latest_at_or_before(3),
        "corrupt": store.corrupt_rows(),
        "metrics": store.event_metrics({"all": 1.0e9}),
    }


def test_a_missing_store_is_refused_and_nothing_is_created(tmp_path: Path) -> None:
    with pytest.raises(ReadOnlyOpenRefused, match="missing event store: .*creates nothing"):
        EventLogReader(tmp_path / "hub.db")

    assert list(tmp_path.iterdir()) == []


def test_a_directory_at_the_store_path_is_refused_before_sqlite_opens_it(tmp_path: Path) -> None:
    """Anything but a regular file is refused first, so a FIFO could never block the open."""
    store = tmp_path / "hub.db"
    store.mkdir()

    with pytest.raises(ReadOnlyOpenRefused, match="is not a regular file: .*creates nothing"):
        EventLogReader(store)

    assert list(store.iterdir()) == []


@pytest.mark.parametrize("content", ["empty", "foreign"])
def test_a_file_without_an_event_table_is_refused_unchanged(tmp_path: Path, content: str) -> None:
    db = tmp_path / "hub.db"
    if content == "empty":
        db.touch()
    else:
        _rewrite(db, "CREATE TABLE notes (body TEXT)")
    before = _snapshot(db)

    with pytest.raises(ReadOnlyOpenRefused, match="holds no event table"):
        EventLogReader(db)

    assert _snapshot(db) == before


def test_the_reader_returns_what_the_writer_returns_and_changes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _journal(db)
    with EventStore(db) as store:
        expected = _reads(store)
    before = _snapshot(db)

    with EventLogReader(db) as reader:
        assert reader.encrypted is False
        assert _reads(reader) == expected
        assert reader.read_since(0, kinds=["finding"])[0].payload["ü"] == "ä"

    _assert_read_left_only_wal_sidecars(db, before)


def test_a_log_from_before_the_format_floor_is_read_and_left_without_a_record(
    tmp_path: Path,
) -> None:
    """The writer would record the format on open; the reader writes nothing."""
    db = tmp_path / "hub.db"
    _journal(db)
    _rewrite(db, "DROP TABLE store_format", "PRAGMA user_version = 0")
    before = _snapshot(db)

    with EventLogReader(db) as reader:
        assert reader.count() == 4

    _assert_read_left_only_wal_sidecars(db, before)
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'store_format'"
        ).fetchone() == (0,)


def test_a_newer_format_is_refused_before_anything_is_read(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _journal(db)
    _rewrite(
        db,
        f"UPDATE store_format SET floor = {STORE_FORMAT + 1}, floor_release = '9.0.0' WHERE id = 1",
        f"PRAGMA user_version = {STORE_FORMAT + 1}",
    )
    before = _snapshot(db)

    with pytest.raises(StoreFormatRefused):
        EventLogReader(db)

    _assert_read_left_only_wal_sidecars(db, before)


def test_a_live_journal_keeps_its_writer_while_a_reader_streams(tmp_path: Path) -> None:
    """A WAL reader takes no write lock: the hub commits while the cursor is open."""
    db = tmp_path / "hub.db"
    _journal(db)
    with EventStore(db) as writer, EventLogReader(db) as reader:
        with reader.stream_events() as rows:
            first = next(rows)
            committed = writer.append("chat", {"sender": "PROJ/bob", "text": "while read"})
        assert first.seq == 1
        assert reader.max_seq() == committed
        assert reader.read_since(committed - 1)[0].payload["text"] == "while read"


def test_a_corrupt_row_comes_back_as_the_writers_marker(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _journal(db)
    _rewrite(db, "UPDATE events SET payload = '{' WHERE seq = 2")
    with EventStore(db) as store:
        expected = (store.read_all(), store.corrupt_rows())
    before = _snapshot(db)

    with EventLogReader(db) as reader:
        assert (reader.read_all(), reader.corrupt_rows()) == expected

    assert [marker.seq for marker in expected[1]] == [2]
    _assert_read_left_only_wal_sidecars(db, before)


def test_the_reader_returns_a_committed_operation_as_the_writer_does(tmp_path: Path) -> None:
    """Replay's protected restorers read an operation through either log."""
    db = tmp_path / "hub.db"
    with EventStore(db) as store:
        store.commit_operation(
            operation_key="seat\0claim\0op-1",
            request_digest="a" * 64,
            response={"type": "claim_result", "accepted": True},
            events=(("claim", {"task_id": "T-1"}),),
            intent={"family": "claim"},
        )
        expected = store.get_operation("seat\0claim\0op-1")
    assert expected is not None
    before = _snapshot(db)

    with EventLogReader(db) as reader:
        assert reader.get_operation("seat\0claim\0op-1") == expected
        assert reader.get_operation("seat\0claim\0absent") is None

    _assert_read_left_only_wal_sidecars(db, before)


def test_the_reader_offers_no_write(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _journal(db)
    with EventLogReader(db) as reader:
        for name in ("append", "append_batch", "delete", "vacuum", "enable_row_mac", "delivery"):
            assert not hasattr(reader, name), name


def test_an_event_query_opens_the_journal_read_only(tmp_path: Path) -> None:
    """Before, the query opened the hub's writer and recorded a format on an older log."""
    db = tmp_path / "hub.db"
    _journal(db)
    _rewrite(db, "DROP TABLE store_format", "PRAGMA user_version = 0")
    before = _snapshot(db)

    result = event_query.run_query(db, "path src/auth.py between 0 35")

    assert result is not None
    _assert_read_left_only_wal_sidecars(db, before)


def test_an_encrypted_log_is_read_with_its_key_and_refused_without_it(tmp_path: Path) -> None:
    """With a SQLCipher driver an encrypted log reads with its key and is refused without it.

    Without the driver no encrypted log can be written, and a keyed read of a
    plaintext journal is refused with the documented error and changes nothing.
    """
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "journal" / "hub.db"
    db.parent.mkdir()
    if not sqlcipher_available():
        with pytest.raises(SqlCipherUnavailableError, match="sqlcipher"):
            EventStore(db, key_file=key)
        assert not db.exists()
        _journal(db)
        before = _snapshot(db)
        with pytest.raises(SqlCipherUnavailableError, match="sqlcipher"):
            EventLogReader(db, key_file=key)
        assert _snapshot(db) == before
        return
    _journal(db, key_file=key)
    with EventStore(db, key_file=key) as store:
        expected = _reads(store)
    before = _snapshot(db)

    with EventLogReader(db, key_file=key) as reader:
        assert reader.encrypted is True
        assert _reads(reader) == expected
    with pytest.raises(SqlCipherKeyError):
        EventLogReader(db)

    _assert_read_left_only_wal_sidecars(db, before)
