# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a stopped or first walk leaves no partial or stray mailbox rows behind

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core import mailbox_layout_walk as walk
from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_layout_counts import count_after
from synapse_channel.core.mailbox_layout_schema import layout_version
from synapse_channel.core.mailbox_pending import MailboxPendingTracker
from synapse_channel.core.persistence import EventStore

_TARGETS = ("P/a", "P/b", "P/a", "P/b", "P/a", "P/b", "P/a")


class _WalkStopped(RuntimeError):
    """Raised by the test inside the walk, after some pages were committed."""


def _chat(store: EventStore, target: str) -> int:
    return store.append(
        EventKind.CHAT, {"sender": "PEER", "target": target, "type": "chat", "payload": "x"}
    )


def _rows(store: EventStore, table: str) -> int:
    row = store.storage.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0])


def test_a_walk_stopped_between_pages_is_walked_again_from_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        for target in _TARGETS:
            _chat(store, target)
        store.storage.connection.execute("DELETE FROM mailbox_address_hits")
        store.storage.connection.execute("DELETE FROM mailbox_name_mentions")
        store.storage.connection.commit()
        original = walk._insert_hits

        def stop_at_the_fifth(connection: Any, seq: int, payload: dict[str, Any]) -> None:
            if seq == 5:
                raise _WalkStopped("the walk stopped after two committed pages")
            original(connection, seq, payload)

        monkeypatch.setattr(walk, "_PAGE", 2)
        monkeypatch.setattr(walk, "_insert_hits", stop_at_the_fifth)
        with pytest.raises(_WalkStopped):
            MailboxPendingTracker(store)
        assert layout_version(store.storage.connection) == 0
        reader = sqlite3.connect(path)
        try:
            committed = reader.execute("SELECT COUNT(*) FROM mailbox_address_hits").fetchone()
        finally:
            reader.close()
        assert committed == (4,)
        monkeypatch.undo()
    finally:
        store.close()
    store = EventStore(path)
    try:
        tracker = MailboxPendingTracker(store)
        assert tracker.known_identities == ("P/b", "P/a")
        connection = store.storage.connection
        assert layout_version(connection) == 1
        assert _rows(store, "mailbox_address_hits") == len(_TARGETS)
        assert _rows(store, "mailbox_name_mentions") == len(_TARGETS)
        assert tracker.pending_count("P/a") == 4 == count_after(connection, "P/a", 0)
        assert tracker.pending_count("P/b") == 3 == count_after(connection, "P/b", 0)
    finally:
        store.close()


def test_a_first_walk_drops_rows_written_before_it(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        _chat(store, "P/a")
        _chat(store, "P/b")
        connection = store.storage.connection
        assert layout_version(connection) == 0
        connection.execute(
            "INSERT INTO mailbox_address_hits (seq, address, sender) VALUES (99, 'P/a', 'PEER')"
        )
        connection.execute(
            "INSERT INTO mailbox_pending_counts (identity, pending, watermark) "
            "VALUES ('P/a', 40, 0)"
        )
        connection.execute(
            "INSERT INTO mailbox_name_mentions (seq, position, name) VALUES (99, 0, 'P/z')"
        )
        connection.commit()
        tracker = MailboxPendingTracker(store)
        assert tracker.known_identities == ("P/a", "P/b")
        assert layout_version(connection) == 1
        assert connection.execute(
            "SELECT seq, address FROM mailbox_address_hits ORDER BY seq"
        ).fetchall() == [(1, "P/a"), (2, "P/b")]
        assert connection.execute(
            "SELECT identity, pending FROM mailbox_pending_counts ORDER BY identity"
        ).fetchall() == [("P/a", 1), ("P/b", 1)]
        assert tracker.pending_count("P/a") == 1
    finally:
        store.close()


def test_the_walk_reads_only_chats_and_watermarks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        for target in _TARGETS:
            _chat(store, target)
            store.append("claim", {"task_id": "t", "agent": "P/a"})
        store.append(
            EventKind.MAILBOX_WATERMARK, {"identity": "P/a", "through_seq": 3, "source": "cursor"}
        )
        read_window = store.read_window
        kinds_read: list[object] = []
        kinds_returned: set[str] = set()

        def recorded(**kwargs: Any) -> Any:
            kinds_read.append(kwargs.get("kinds"))
            events = read_window(**kwargs)
            kinds_returned.update(str(event.kind) for event in events)
            return events

        monkeypatch.setattr(walk, "_PAGE", 3)
        monkeypatch.setattr(store, "read_window", recorded)
        tracker = MailboxPendingTracker(store)
        assert kinds_read
        assert all(kinds == ("chat", "mailbox_watermark") for kinds in kinds_read)
        assert kinds_returned == {"chat", "mailbox_watermark"}
        assert tracker.known_identities == ("P/b", "P/a")
        assert tracker.pending_count("P/a") == 3
        assert tracker.pending_count("P/b") == 3
    finally:
        store.close()


def test_an_event_of_another_kind_is_not_projected_even_when_the_window_returns_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk counts chats only, whatever the store's window hands it."""
    store = EventStore(tmp_path / "hub.db")
    try:
        for target in ("P/a", "P/b", "P/a"):
            _chat(store, target)
        store.append("claim", {"sender": "PEER", "target": "P/a", "type": "chat", "payload": "x"})
        read_window = store.read_window

        def every_kind(**kwargs: Any) -> Any:
            kwargs.pop("kinds", None)
            return read_window(**kwargs)

        monkeypatch.setattr(store, "read_window", every_kind)
        tracker = MailboxPendingTracker(store)
        assert tracker.pending_count("P/a") == 2
        assert tracker.pending_count("P/b") == 1
        assert _rows(store, "mailbox_address_hits") == 3
        assert _rows(store, "mailbox_name_mentions") == 3
    finally:
        store.close()
