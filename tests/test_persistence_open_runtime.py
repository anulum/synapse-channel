# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real failed-constructor ownership and recovery tests
"""Exercise failed event-store opens without replacing database connections."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.delivery_persistence import DELIVERY_ACCEPTED
from synapse_channel.core.persistence import EventStore

_EVENTS = (
    "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL NOT NULL, "
    "kind TEXT NOT NULL, payload TEXT NOT NULL)"
)
_MARKER = '{"marker":"kept"}'


def _assert_released(path: Path) -> None:
    """Check actual WAL ownership and independent writer access before recovery."""
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()
    connection = sqlite3.connect(path, timeout=0)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.commit()
        assert connection.execute("SELECT seq FROM events").fetchall() == [(7,)]
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("schema", "event_table", "repair", "detail"),
    [
        (
            "CREATE TABLE events (seq INTEGER PRIMARY KEY)",
            "events",
            (
                "ALTER TABLE events ADD COLUMN ts REAL NOT NULL DEFAULT 1",
                "ALTER TABLE events ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'",
                'ALTER TABLE events ADD COLUMN payload TEXT NOT NULL DEFAULT \'{"marker":"kept"}\'',
            ),
            "no such column: kind",
        ),
        (
            _EVENTS + "; CREATE TABLE delivery_receipt_outbox (notification_id TEXT PRIMARY KEY)",
            "events",
            ("DROP TABLE delivery_receipt_outbox",),
            "no such column: sender",
        ),
        (
            _EVENTS.replace("TABLE events", "TABLE backing_events")
            + "; CREATE VIEW events AS SELECT * FROM backing_events",
            "backing_events",
            ("DROP VIEW events", "ALTER TABLE backing_events RENAME TO events"),
            "Cannot add a column to a view",
        ),
        (
            _EVENTS + "; CREATE TABLE delivery_requests (operation_key TEXT PRIMARY KEY)",
            "events",
            ("DROP TABLE delivery_requests",),
            "no such column: target",
        ),
        (
            _EVENTS + "; CREATE TABLE message_forward_outbox (forward_id TEXT PRIMARY KEY)",
            "events",
            ("DROP TABLE message_forward_outbox",),
            "no such column: state",
        ),
    ],
)
def test_failed_schema_open_releases_resources_and_preserves_rows(
    tmp_path: Path, schema: str, event_table: str, repair: tuple[str, ...], detail: str
) -> None:
    """Retained tracebacks cannot keep failed early or late opens alive."""
    path = tmp_path / "schema.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(schema)
        columns = len(connection.execute(f"PRAGMA table_info({event_table})").fetchall())
        if columns == 1:
            connection.execute("INSERT INTO events VALUES (7)")
        else:
            connection.execute(
                f"INSERT INTO {event_table} VALUES (?, ?, ?, ?)", (7, 1.0, "chat", _MARKER)
            )
        connection.commit()
    finally:
        connection.close()

    failures: list[BaseException] = []
    for _ in range(3):
        with pytest.raises(sqlite3.OperationalError, match=detail) as failure:
            EventStore(path)
        failures.append(failure.value)
        assert failures[-1].__traceback__ is not None
        _assert_released(path)

    connection = sqlite3.connect(path)
    try:
        for statement in repair:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()
    with EventStore(path) as recovered:
        assert recovered.read_all()[0].payload == {"marker": "kept"}
        assert recovered.append("chat", {"recovered": True}) == 8
    with EventStore(path) as reopened:
        assert [event.seq for event in reopened.read_all()] == [7, 8]


@pytest.mark.parametrize("failure_type", [RuntimeError, BaseException])
def test_interrupted_open_rolls_back_backfill_and_preserves_exception(
    tmp_path: Path, failure_type: type[BaseException]
) -> None:
    """A real caller iterable interruption releases an uncommitted backfill."""
    path = tmp_path / "interrupted.db"
    payload = {"message_seq": 7, "message_id": 77, "sender": "sender", "target": "target"}
    with EventStore(path) as initial:
        initial.append("delivery_receipt_requested", payload)
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE events SET seq = 7")
        connection.commit()
    finally:
        connection.close()
    interruption = failure_type("caller interrupted initialization")

    def interrupted_kinds() -> Iterator[str]:
        """Interrupt the public iterable after one supported event kind."""
        yield "chat"
        raise interruption

    with pytest.raises(failure_type) as failure:
        EventStore(path, aef_outbox_kinds=interrupted_kinds())
    assert failure.value is interruption
    assert interruption.__traceback__ is not None
    _assert_released(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM delivery_receipts").fetchone() == (0,)
    finally:
        connection.close()
    with EventStore(path, aef_outbox_kinds=("chat",)) as recovered:
        assert recovered.read_all()[0].payload == payload
        assert recovered.append("chat", {"recovered": True}) == 8
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM delivery_receipts").fetchone() == (1,)
    finally:
        connection.close()


def test_failed_replay_open_releases_resources_without_accepting_invalid_events(
    tmp_path: Path,
) -> None:
    """The final replay refusal also closes its handle and retains the bad row."""
    path = tmp_path / "replay.db"
    with EventStore(path) as initial:
        initial.append(DELIVERY_ACCEPTED, {"profile": 2})
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE events SET seq = 7")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DeliveryRefusal) as failure:
        EventStore(path)
    assert failure.value.code == "replay_incompatible"
    _assert_released(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT payload FROM events").fetchone() == ('{"profile":2}',)
    finally:
        connection.close()


def test_successful_open_keeps_connection_until_explicit_close(tmp_path: Path) -> None:
    """Successful construction transfers ownership to the public store caller."""
    path = tmp_path / "success.db"
    store = EventStore(path)
    assert store.append("chat", {"marker": "kept"}) == 1
    assert Path(f"{path}-wal").exists()
    assert store.read_all()[0].payload == {"marker": "kept"}
    store.close()
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()
    with EventStore(path) as reopened:
        assert reopened.append("chat", {"reopened": True}) == 2
        assert [event.seq for event in reopened.read_all()] == [1, 2]
