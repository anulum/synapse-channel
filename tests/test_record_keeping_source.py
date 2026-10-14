# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real journal snapshot and authentication tests
"""Exercise the existing-journal reader through actual SQLite and SQLCipher."""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from pathlib import Path
from typing import TypedDict

import pytest

from synapse_channel.core.event_row_mac import load_or_create_row_mac_key, row_mac_key_path
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
from synapse_channel.core.record_keeping_source import iter_record_source


class _BudgetOptions(TypedDict, total=False):
    through_seq: int
    max_events: int
    max_payload_bytes: int


def test_snapshot_excludes_concurrent_append_and_closes_on_early_exit(tmp_path: Path) -> None:
    path = tmp_path / "active.db"
    store = EventStore(path)
    try:
        store.append("claim", {"task_id": "T", "nested": {"score": 1.5}}, ts=20)
        store.append("release", {"task_id": "T"}, ts=10)
        with contextlib.closing(iter_record_source(path)) as source:
            first = next(source)
            store.append("finding", {"task_id": "T"}, ts=30)
            assert first.event.payload["nested"] == {"score": 1.5}
            assert [row.event.seq for row in source] == [2]
        assert [row.event.seq for row in iter_record_source(path)] == [1, 2, 3]
        with contextlib.closing(iter_record_source(path)) as early:
            assert next(early).event.seq == 1
        with sqlite3.connect(path) as checkpoint:
            assert checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        store.append("release", {"task_id": "T"}, ts=40)
        assert len(list(iter_record_source(path))) == 4
    finally:
        store.close()


def test_existing_legacy_schema_is_not_migrated_or_chmodded(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT)"
        )
        connection.execute("INSERT INTO events VALUES (1, -1, 'claim', '{\"task_id\":\"legacy\"}')")
    path.chmod(0o640)
    before, mode = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode
    assert list(iter_record_source(path))[0].event.ts == -1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert path.stat().st_mode == mode
    assert not row_mac_key_path(path).exists()
    with sqlite3.connect(path) as connection:
        assert [row[1] for row in connection.execute("PRAGMA table_info(events)")] == [
            "seq",
            "ts",
            "kind",
            "payload",
        ]


def test_row_authentication_distinguishes_legacy_prefix_and_refuses_tampering(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authenticated.db"
    store = EventStore(path)
    try:
        store.append("claim", {"task_id": "legacy"}, ts=1)
        key = load_or_create_row_mac_key(
            row_mac_key_path(path), current_max_seq=1, log_has_macs=False
        )
        assert store.enable_row_mac(key) == ()
        store.append("release", {"task_id": "signed"}, ts=2)
        assert [row.authenticated for row in iter_record_source(path, row_mac=key)] == [False, True]
        with pytest.raises(ValueError, match="requires its existing row key"):
            list(iter_record_source(path, through_seq=0))
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE events SET payload='{}' WHERE seq=2")
        with pytest.raises(ValueError, match="authentication failed"):
            list(iter_record_source(path, row_mac=key))
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE events SET mac='😃' WHERE seq=2")
        with pytest.raises(ValueError, match="authentication failed"):
            list(iter_record_source(path, row_mac=key))
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE events SET mac=NULL WHERE seq=2")
        with pytest.raises(ValueError, match="authentication failed"):
            list(iter_record_source(path, row_mac=key))
    finally:
        store.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("seq", -1),
        ("ts", "invalid-clock"),
        ("ts", float("inf")),
        ("kind", ""),
        ("kind", "bad\nkind"),
        ("kind", "corrupt_event"),
        ("payload", b"{}"),
        ("payload", None),
        ("payload", "[]"),
        ("payload", '{"x":1,"x":2}'),
        ("payload", '{"x":{"y":1,"y":2}}'),
        ("payload", '{"x":NaN}'),
        ("payload", '{"x":Infinity}'),
        ("payload", '{"x":1e999}'),
        ("payload", "{broken}"),
    ],
)
def test_invalid_persisted_row_refuses_whole_read(
    tmp_path: Path, field: str, value: object
) -> None:
    path = tmp_path / "tampered.db"
    store = EventStore(path)
    store.append("claim", {"task_id": "T"}, ts=1)
    store.close()
    assert field in {"seq", "ts", "kind", "payload"}
    with sqlite3.connect(path) as connection:
        if value is None:
            connection.execute("ALTER TABLE events RENAME TO original_events")
            connection.execute(
                "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT)"
            )
            connection.execute(
                "INSERT INTO events SELECT seq, ts, kind, payload FROM original_events"
            )
        connection.execute(f"UPDATE events SET {field}=?", (value,))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        list(iter_record_source(path))
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "options",
    [
        {"through_seq": -1},
        {"through_seq": True},
        {"through_seq": 2**63},
        {"max_events": 0},
        {"max_events": False},
        {"max_payload_bytes": -1},
    ],
)
def test_invalid_budgets_do_not_create_missing_store(
    tmp_path: Path, options: _BudgetOptions
) -> None:
    path = tmp_path / "absent" / "hub.db"
    with pytest.raises(ValueError):
        list(iter_record_source(path, **options))
    assert not path.parent.exists()


def test_complete_export_budgets_never_silently_truncate(tmp_path: Path) -> None:
    path = tmp_path / "budget.db"
    store = EventStore(path)
    store.append_batch((("claim", {"task_id": "T"}), ("release", {"task_id": "T"})), ts=1)
    store.close()
    with pytest.raises(ValueError, match="event budget"):
        list(iter_record_source(path, max_events=1))
    with pytest.raises(ValueError, match="byte budget"):
        list(iter_record_source(path, max_payload_bytes=15))
    assert len(list(iter_record_source(path, max_events=2, max_payload_bytes=30))) == 2
    assert list(iter_record_source(path, through_seq=0)) == []


def test_missing_unreadable_and_incompatible_journals_stay_unchanged(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(ValueError, match="existing event journal"):
        list(iter_record_source(missing))
    assert not missing.exists()
    incompatible = tmp_path / "schema.db"
    with sqlite3.connect(incompatible) as connection:
        connection.execute("CREATE TABLE events (seq TEXT, ts REAL, kind TEXT, payload TEXT)")
    with pytest.raises(ValueError, match="schema"):
        list(iter_record_source(incompatible))
    composite = tmp_path / "composite.db"
    with sqlite3.connect(composite) as connection:
        connection.execute(
            "CREATE TABLE events (seq INTEGER, ts REAL, kind TEXT, payload TEXT, "
            "PRIMARY KEY (seq, kind))"
        )
        connection.execute(
            "INSERT INTO events VALUES (1, 1, 'claim', '{}'), (1, 2, 'release', '{}')"
        )
    with pytest.raises(ValueError, match="schema"):
        list(iter_record_source(composite))
    invalid = tmp_path / "private-sentinel.db"
    invalid.write_bytes(b"not a sqlite database")
    with pytest.raises(ValueError, match="event journal cannot be read"):
        list(iter_record_source(invalid))
    assert invalid.read_bytes() == b"not a sqlite database"


def test_encrypted_journal_uses_existing_key_or_reports_native_backend_unavailable(
    tmp_path: Path,
) -> None:
    key = tmp_path / "cipher.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    path = tmp_path / "cipher.db"
    if not sqlcipher_available():
        store = EventStore(path)
        store.close()
        with pytest.raises(RuntimeError):
            list(iter_record_source(path, key_file=key))
        return
    store = EventStore(path, key_file=key)
    store.append("claim", {"task_id": "encrypted"}, ts=1)
    store.close()
    before = path.read_bytes()
    assert list(iter_record_source(path, key_file=key))[0].event.payload == {"task_id": "encrypted"}
    assert path.read_bytes() == before
    wrong = tmp_path / "wrong.key"
    wrong.write_bytes(b"w" * 32)
    wrong.chmod(0o600)
    with pytest.raises(ValueError):
        list(iter_record_source(path, key_file=wrong))
