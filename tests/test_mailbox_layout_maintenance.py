# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a chat or a deletion touches only the mailbox counters it can reach

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.core import mailbox_layout_maintenance as maintenance
from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_layout_counts import count_after, remember_count
from synapse_channel.core.mailbox_pending import MailboxPendingTracker
from synapse_channel.core.persistence import EventStore

_NAMED = 5000
_IDENTITIES = (
    "P",
    "P/ann",
    "P/annex",
    "P/ann/sub",
    "PX/ann",
    "P-x/ann",
    "Q/dee",
    "role:P/reviewer",
    "role:P",
)
_TARGETS = (
    ("PEER", "P/ann"),
    ("PEER", "P"),
    ("PEER", "P/reviewer"),
    ("PEER", "P/a?n"),
    ("PEER", "P/ann,P"),
    ("PEER", "PX/ann,Q/dee"),
    ("P/ann", "P"),
    ("P/ann", "P/ann,P/annex"),
    ("PEER", "P/ann/sub"),
    ("PEER", "P-x/ann,P/[ab]nnex"),
    ("PEER", "P/reviewer,P"),
    ("PEER", "Q"),
    ("PEER", "P/*"),
    ("PEER", "seat@OTHER,P/annex"),
)


def _chat(store: EventStore, target: str, sender: str = "PEER") -> int:
    return store.append(
        EventKind.CHAT,
        {"sender": sender, "target": target, "type": "chat", "payload": f"to {target}"},
    )


def _named_store(path: Path) -> EventStore:
    store = EventStore(path)
    store.append_batch(
        [
            ("chat", {"sender": "PEER", "target": f"P/m{index}", "type": "chat", "payload": "x"})
            for index in range(_NAMED)
        ]
    )
    MailboxPendingTracker(store)
    return store


def _counting(monkeypatch: pytest.MonkeyPatch, name: str, checked: list[str]) -> None:
    """Record every mailbox the named matcher is asked about."""
    original = getattr(maintenance, name)

    def counted(address_or_addresses: object, identity: str) -> bool:
        checked.append(identity)
        return bool(original(address_or_addresses, identity))

    monkeypatch.setattr(maintenance, name, counted)


def _assert_counters_equal_a_recount(store: EventStore) -> None:
    connection = store.storage.connection
    rows = connection.execute(
        "SELECT identity, pending, watermark FROM mailbox_pending_counts"
    ).fetchall()
    assert rows
    for identity, pending, watermark in rows:
        assert pending == count_after(connection, str(identity), int(watermark)), identity


def test_an_exact_chat_reads_only_the_counter_it_reaches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _named_store(tmp_path / "hub.db")
    try:
        checked: list[str] = []
        _counting(monkeypatch, "any_reaches", checked)
        _chat(store, "P/m7")
        assert checked == ["P/m7"]
        assert count_after(store.storage.connection, "P/m7", 0) == 2
    finally:
        store.close()


def test_a_deleted_chat_reads_only_the_counter_it_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _named_store(tmp_path / "hub.db")
    try:
        seq = _chat(store, "P/m9")
        checked: list[str] = []
        _counting(monkeypatch, "reaches", checked)
        assert store.delete([seq]) == 1
        assert checked == ["P/m9"]
        row = store.storage.connection.execute(
            "SELECT pending FROM mailbox_pending_counts WHERE identity = 'P/m9'"
        ).fetchone()
        assert row == (1,)
    finally:
        store.close()


def test_a_glob_part_still_reaches_every_counter_it_matches(tmp_path: Path) -> None:
    store = _named_store(tmp_path / "hub.db")
    try:
        _chat(store, "P/m1?")
        connection = store.storage.connection
        bumped = connection.execute(
            "SELECT COUNT(*) FROM mailbox_pending_counts WHERE pending = 2"
        ).fetchone()
        assert bumped == (10,)
    finally:
        store.close()


def test_stored_counters_equal_a_recount_after_every_append_and_delete(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        _chat(store, "P/ann")
        MailboxPendingTracker(store)
        connection = store.storage.connection
        for identity in _IDENTITIES:
            remember_count(connection, identity, count_after(connection, identity, 0), 0)
        connection.commit()
        appended: list[int] = []
        for sender, target in _TARGETS:
            appended.append(_chat(store, target, sender))
            _assert_counters_equal_a_recount(store)
        for seq in appended[::3]:
            assert store.delete([seq]) == 1
            _assert_counters_equal_a_recount(store)
    finally:
        store.close()


def _schema_statements(store: EventStore, action: Callable[[], object]) -> list[str]:
    """Run ``action`` and return the schema statements the store's connection ran."""
    statements: list[str] = []
    connection = store.storage.connection
    connection.set_trace_callback(statements.append)
    try:
        action()
    finally:
        connection.set_trace_callback(None)
    return [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("CREATE", "ALTER", "PRAGMA TABLE_INFO"))
    ]


def test_a_chat_a_watermark_a_read_and_a_delete_run_no_schema_statement(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        first = _chat(store, "P/ann")
        tracker = MailboxPendingTracker(store)
        assert _schema_statements(store, lambda: _chat(store, "P/ann")) == []
        assert _schema_statements(store, lambda: tracker.pending_count("P/ann")) == []
        assert (
            _schema_statements(store, lambda: tracker.advance("P/ann", first, source="cursor"))
            == []
        )
        assert _schema_statements(store, lambda: store.delete([first])) == []
        assert tracker.pending_count("P/ann") == 1
    finally:
        store.close()
