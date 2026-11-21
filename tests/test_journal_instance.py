# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — journal instance tests
"""A journal's instance is written once, by a writer, and an ordinary open never changes it."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_reader import EventLogReader
from synapse_channel.core.persistence_store_format import JournalInstance

_HEX32 = re.compile(r"[0-9a-f]{32}")


def _rewrite(path: Path, *statements: str) -> None:
    """Apply what an earlier release left, then close the connection."""
    with closing(sqlite3.connect(path)) as connection:
        for statement in statements:
            connection.execute(statement)
        connection.commit()


def _journal(path: Path, events: int) -> JournalInstance:
    with EventStore(path) as store:
        for index in range(events):
            store.append("chat", {"sender": "PROJ/alice", "text": f"message {index}"})
        instance = store.journal_instance()
    assert instance is not None
    return instance


def test_a_new_journal_gets_a_random_instance_from_floor_zero(tmp_path: Path) -> None:
    first = _journal(tmp_path / "a.db", 2)
    second = _journal(tmp_path / "b.db", 2)

    assert _HEX32.fullmatch(first.instance)
    assert first.floor == 0
    assert first.instance != second.instance


def test_a_reopen_keeps_the_instance_and_the_bytes(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    created = _journal(db, 3)
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    with EventStore(db) as store:
        reopened = store.journal_instance()
        store.append("chat", {"sender": "PROJ/bob", "text": "after the reopen"})
    with EventStore(db) as store:
        assert store.journal_instance() == created

    assert reopened == created
    with closing(sqlite3.connect(db)) as connection:
        stored = connection.execute(
            "SELECT journal_instance, instance_floor FROM store_format WHERE id = 1"
        ).fetchone()
    assert stored == (created.instance, created.floor)
    assert before != hashlib.sha256(db.read_bytes()).hexdigest()  # the append did change the log


def test_a_journal_from_before_the_format_record_gets_one_at_its_floor(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _journal(db, 4)
    _rewrite(db, "DROP TABLE store_format", "PRAGMA user_version = 0")

    with EventStore(db) as store:
        instance = store.journal_instance()

    assert instance is not None
    assert instance.floor == 4


def test_a_record_without_the_instance_columns_gains_them_on_a_writer_open(
    tmp_path: Path,
) -> None:
    """A record the format unit wrote before the instance existed is completed, not replaced."""
    db = tmp_path / "hub.db"
    _journal(db, 5)
    _rewrite(
        db,
        "ALTER TABLE store_format DROP COLUMN journal_instance",
        "ALTER TABLE store_format DROP COLUMN instance_floor",
    )
    with EventLogReader(db) as reader:
        assert reader.journal_instance() is None

    with EventStore(db) as store:
        instance = store.journal_instance()

    assert instance is not None
    assert instance.floor == 5
    with closing(sqlite3.connect(db)) as connection:
        assert (
            connection.execute(
                "SELECT floor, floor_release FROM store_format WHERE id = 1"
            ).fetchone()[0]
            == 1
        )


def test_the_reader_reports_the_instance_and_never_writes_one(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    created = _journal(db, 2)
    with EventLogReader(db) as reader:
        assert reader.journal_instance() == created

    _rewrite(db, "DROP TABLE store_format", "PRAGMA user_version = 0")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with EventLogReader(db) as reader:
        assert reader.journal_instance() is None

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
