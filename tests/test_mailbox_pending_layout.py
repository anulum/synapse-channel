# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable mailbox counts stay equal to the historical scan

from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.journal import EventKind, record_chat
from synapse_channel.core.journal_export import NOT_INCLUDED, export_to_sqlite
from synapse_channel.core.mailbox_pending import MailboxPendingTracker
from synapse_channel.core.mailbox_pending_layout import (
    MailboxLayoutRefused,
    count_between,
    layout_version,
    project_mailbox_layout,
    record_deleted_sequences,
    record_inserted_event,
    remember_count,
)
from synapse_channel.core.mailbox_pending_layout import (
    _project_event as project_one_event,
)
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.persistence_sqlcipher import (
    migrate_plaintext_to_sqlcipher,
    sqlcipher_available,
)
from synapse_channel.core.role_mailbox import mailbox_role

_IDENTITIES = ("P/ann", "P/bob", "Q/dee", "P", "role:P/reviewer")
_TARGETS = (
    "P/ann",
    "P/bob",
    "Q/dee",
    "P",
    "P/reviewer",
    "all",
    "P/*",
    "P/b?",
    "P/a?n",
    "Q/*",
    "P/ann,P/bob",
    "P/bob,P",
    "P/ann,P/a?n",
    "P/a?n,P/an?",
    "seat@OTHER",
    "P/ann,seat@OTHER",
    ",",
)
_SENDERS = ("P/ann", "P/bob", "Q/dee", "PEER", "role:P/reviewer")
_LOOP_BOUND_SECONDS = 0.25


def _chat(store: EventStore, *, sender: str = "PEER", target: str = "PROJ/BOB") -> int:
    return store.append(
        EventKind.CHAT,
        {"sender": sender, "target": target, "type": "chat", "payload": f"to {target}"},
    )


def _reference_reaches(target: str, identity: str) -> bool:
    """The historical scan: one directed chat reaches a mailbox once."""
    role = mailbox_role(identity)
    project = identity.split("/", 1)[0]
    for raw in target.split(","):
        part = raw.strip()
        if not part:
            continue
        if role is not None:
            if fnmatchcase(role, part):
                return True
            continue
        if any(marker in part for marker in "*?["):
            if fnmatchcase(identity, part) or fnmatchcase(project, part):
                return True
        elif part == identity or part == project:
            return True
    return False


def _reference_pending(events: list[StoredEvent], identity: str, watermark: int) -> int:
    """Count the way the cold scan did, from the events still in the journal."""
    total = 0
    for event in events:
        if event.kind != EventKind.CHAT or event.seq <= watermark:
            continue
        payload = event.payload
        target = str(payload.get("target") or "all")
        if payload.get("channel") or not is_directed_target(target):
            continue
        if str(payload.get("sender") or "") == identity:
            continue
        if _reference_reaches(target, identity):
            total += 1
    return total


def _assert_matches(store: EventStore, tracker: MailboxPendingTracker) -> None:
    tracker.snapshot(("P/ann",), lambda _name: ("P/reviewer",))
    assert tracker.pending_count("P/reviewer") == 0
    events = store.read_since(0)
    for identity in _IDENTITIES:
        watermark = tracker.watermark_for(identity)
        assert tracker.pending_count(identity) == _reference_pending(events, identity, watermark)


def test_export_leaves_mailbox_counts_out_of_the_portable_file() -> None:
    assert any("mailbox pending counts" in item for item in NOT_INCLUDED)
    assert "not a hub store" in (export_to_sqlite.__doc__ or "")


def test_a_relayed_directed_chat_is_counted(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        record_chat(
            store,
            {
                "sender": "PEER",
                "target": "PROJ/BOB",
                "type": "chat",
                "payload": "forwarded",
                "forwarded_from": "seat@OTHER",
                "forward_id": "relay-1",
            },
        )
        tracker = MailboxPendingTracker(store)
        assert tracker.pending_count("PROJ/BOB") == 1
        assert "seat@OTHER" not in tracker.known_identities
    finally:
        store.close()


def test_a_hub_qualified_part_is_stored_and_is_not_a_local_mailbox(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        _chat(store, target="seat@OTHER")
        tracker = MailboxPendingTracker(store)
        assert tracker.known_identities == ()
        hit = store.storage.connection.execute(
            "SELECT address FROM mailbox_address_hits"
        ).fetchone()
        assert hit is not None and hit[0] == "seat@OTHER"
    finally:
        store.close()


def test_comma_and_glob_parts_count_a_chat_once(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        _chat(store, target="PROJ/BOB,PROJ")
        _chat(store, target="PROJ/BOB,PROJ/BO?")
        _chat(store, target="PROJ/BO?,PROJ/B?B")
        _chat(store, target="PROJ/*")
        _chat(store, target="NO/zz?")
        store.append(
            EventKind.MAILBOX_WATERMARK,
            {"identity": "PROJ/BOB", "through_seq": True, "source": "cursor"},
        )
        tracker = MailboxPendingTracker(store)
        assert tracker.pending_count("PROJ/BOB") == 3
        assert tracker.pending_count("NO/ann") == 0
    finally:
        store.close()


def test_deleting_a_counted_chat_matches_the_scan(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        first = _chat(store)
        second = _chat(store)
        tracker = MailboxPendingTracker(store)
        assert tracker.advance("PROJ/BOB", first, source="cursor") is True
        assert store.delete([first]) == 1
        events = store.read_since(0)
        assert tracker.pending_count("PROJ/BOB") == _reference_pending(
            events, "PROJ/BOB", tracker.watermark_for("PROJ/BOB")
        )
        assert store.delete([second]) == 1
        events = store.read_since(0)
        assert tracker.pending_count("PROJ/BOB") == 0
        assert _reference_pending(events, "PROJ/BOB", tracker.watermark_for("PROJ/BOB")) == 0
        assert store.delete([]) == 0
    finally:
        store.close()


def test_a_sequence_the_delete_keeps_keeps_its_hit(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        first = _chat(store)
        second = _chat(store)
        protected = store.append("protected_write", {"kept": True})
        tracker = MailboxPendingTracker(store)
        store.storage.connection.execute(
            "INSERT INTO mailbox_address_hits (seq, address, sender) VALUES (?, ?, ?)",
            (protected, "PROJ/BOB", "PEER"),
        )
        store.storage.connection.commit()
        assert store.delete([first, protected]) == 1
        left = {
            int(row[0])
            for row in store.storage.connection.execute("SELECT seq FROM mailbox_address_hits")
        }
        assert left == {second, protected}
        assert tracker.pending_count("PROJ/BOB") == 1
        assert store.read_window(min_seq=protected, max_seq=protected)
    finally:
        store.close()


def test_an_unknown_layout_refuses_startup_without_rewriting_rows(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    EventStore(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DELETE FROM mailbox_pending_layout")
    raw.execute("INSERT INTO mailbox_pending_layout (version) VALUES (2)")
    raw.execute(
        "INSERT INTO mailbox_pending_counts (identity, pending, watermark) VALUES ('KEEP', 4, 9)"
    )
    raw.commit()
    raw.close()
    with pytest.raises(MailboxLayoutRefused):
        EventStore(path)
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("SELECT version FROM mailbox_pending_layout").fetchone()[0] == 2
        assert raw.execute("SELECT pending FROM mailbox_pending_counts").fetchone()[0] == 4
    finally:
        raw.close()


def test_a_non_integer_layout_version_refuses_startup(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    EventStore(path).close()
    raw = sqlite3.connect(path)
    raw.execute("INSERT INTO mailbox_pending_layout (version) VALUES ('no')")
    raw.commit()
    raw.close()
    with pytest.raises(MailboxLayoutRefused):
        EventStore(path)


def test_a_missing_known_column_is_added_and_an_empty_window_is_zero(tmp_path: Path) -> None:
    path = tmp_path / "partial.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE mailbox_pending_counts (identity TEXT PRIMARY KEY)")
    raw.execute(
        "CREATE TABLE mailbox_address_hits ("
        "seq INTEGER NOT NULL, address TEXT NOT NULL, PRIMARY KEY(seq, address))"
    )
    from synapse_channel.core.mailbox_pending_layout import ensure_mailbox_schema

    ensure_mailbox_schema(raw)
    columns = {str(row[1]) for row in raw.execute("PRAGMA table_info(mailbox_pending_counts)")}
    assert {"pending", "watermark"} <= columns
    sender = {str(row[1]) for row in raw.execute("PRAGMA table_info(mailbox_address_hits)")}
    assert "sender" in sender
    assert count_between(raw, "P/ann", 5, 1) == 0
    raw.close()


def test_inserted_rows_ignore_bad_payloads_and_a_closed_window(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        connection = store.storage.connection
        remember_count(connection, "PROJ/BOB", 0, 50)
        record_inserted_event(connection, 10, EventKind.CHAT, "{")
        record_inserted_event(connection, 11, EventKind.CHAT, "[]")
        record_inserted_event(
            connection,
            12,
            EventKind.CHAT,
            json.dumps({"sender": "PEER", "target": "PROJ/BOB", "type": "chat", "payload": "x"}),
        )
        record_inserted_event(connection, 13, "claim", "{}")
        record_inserted_event(
            connection,
            60,
            EventKind.MAILBOX_WATERMARK,
            json.dumps({"identity": "", "through_seq": 1, "source": "cursor"}),
        )
        record_inserted_event(
            connection,
            61,
            EventKind.MAILBOX_WATERMARK,
            json.dumps({"identity": "PROJ/BOB", "through_seq": True, "source": "cursor"}),
        )
        record_inserted_event(connection, 62, EventKind.MAILBOX_WATERMARK, "{")
        record_inserted_event(
            connection,
            63,
            EventKind.MAILBOX_WATERMARK,
            json.dumps({"identity": "PROJ/BOB", "through_seq": 1, "source": "cursor"}),
        )
        record_deleted_sequences(connection, [])
        connection.commit()
        row = connection.execute(
            "SELECT pending, watermark FROM mailbox_pending_counts WHERE identity = 'PROJ/BOB'"
        ).fetchone()
        assert row == (0, 50)
        project_one_event(
            connection,
            SimpleNamespace(kind=EventKind.CHAT, seq=1, payload=["not", "an", "object"]),
            {},
            set(),
            [],
        )
    finally:
        store.close()


def test_the_walk_logs_and_finishes_before_the_hub_listens(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        _chat(store, target="P/ann")
        caplog.set_level(logging.INFO, logger="synapse.hub")
        hub = SynapseHub.from_config(HubConfig(journal=store, hub_id="pending-layout"))
        try:
            assert hub._bound_address is None
            assert "mailbox pending layout walk started event_rows=1" in caplog.text
            assert "mailbox pending layout walk finished event_rows=1 hit_rows=1" in caplog.text
            assert layout_version(store.storage.connection) == 1
            caplog.clear()
            MailboxPendingTracker(store)
            assert "mailbox pending layout walk started" not in caplog.text
            project_mailbox_layout(store)
        finally:
            hub.close()
    finally:
        store.close()


async def test_a_cold_count_and_a_warm_canary_block_under_a_quarter_second(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        payload = {"sender": "PEER", "target": "P", "type": "chat", "payload": "x"}
        store.append_batch([("chat", payload) for _ in range(1000)])
        tracker = MailboxPendingTracker(store)
        assert tracker.pending_count("P") == 1000
        gaps: list[float] = []
        done = asyncio.Event()

        async def tick() -> None:
            previous = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0)
                now = time.perf_counter()
                gaps.append(now - previous)
                previous = now

        running = asyncio.create_task(tick())
        await asyncio.sleep(0)
        counts = tracker.snapshot(("P", "P/ann"), lambda _name: ())
        done.set()
        await running
    finally:
        store.close()
    assert counts == {"P": 1000, "P/ann": 1000}
    assert gaps
    assert max(gaps) < _LOOP_BOUND_SECONDS


def test_a_plain_name_loaded_after_its_role_stays_out_of_the_projection(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        _chat(store, target="P/reviewer")
        tracker = MailboxPendingTracker(store)
        tracker.snapshot(("P/bob",), lambda _name: ("P/reviewer",))
        connection = store.storage.connection
        row = connection.execute(
            "SELECT pending, watermark FROM mailbox_pending_counts WHERE identity = 'P/reviewer'"
        ).fetchone()
        assert row is not None
        connection.execute("DELETE FROM mailbox_pending_counts WHERE identity = 'P/reviewer'")
        connection.execute(
            "INSERT INTO mailbox_pending_counts (identity, pending, watermark) VALUES "
            "('P/reviewer', ?, ?)",
            (int(row[0]), int(row[1])),
        )
        connection.commit()
    finally:
        store.close()
    reopened = EventStore(path)
    try:
        restored = MailboxPendingTracker(reopened)
        assert "P/reviewer" not in restored.known_identities
        assert "role:P/reviewer" in restored.known_identities
    finally:
        reopened.close()


def test_random_chats_acknowledgements_and_deletes_match_the_scan(tmp_path: Path) -> None:
    rng = random.Random(20261116)
    for trial in range(8):
        store = EventStore(tmp_path / f"trial-{trial}.db")
        try:
            _append_random(store, rng)
            if rng.random() < 0.5:
                _delete_one_chat(store, rng)
            tracker = MailboxPendingTracker(store)
            _acknowledge_and_delete(store, tracker, rng)
            _assert_matches(store, tracker)
        finally:
            store.close()
        reopened = EventStore(tmp_path / f"trial-{trial}.db")
        try:
            _assert_matches(reopened, MailboxPendingTracker(reopened))
        finally:
            reopened.close()


def _append_random(store: EventStore, rng: random.Random) -> None:
    for index in range(24):
        target = rng.choice(_TARGETS)
        sender = rng.choice(_SENDERS)
        if index % 11 == 0:
            record_chat(
                store,
                {
                    "sender": sender,
                    "target": target,
                    "type": "chat",
                    "payload": "relayed",
                    "forwarded_from": "seat@OTHER",
                    "forward_id": f"relay-{index}",
                    **({"channel": "ops"} if index % 17 == 0 else {}),
                },
            )
            continue
        payload: dict[str, Any] = {
            "sender": sender,
            "target": target,
            "type": "chat",
            "payload": f"n{index}",
        }
        if index % 13 == 0:
            payload["channel"] = "ops"
        store.append(EventKind.CHAT, payload)
    store.append("claim", {"n": 1})


def _delete_one_chat(store: EventStore, rng: random.Random) -> None:
    chats = [event.seq for event in store.read_since(0) if event.kind == EventKind.CHAT]
    if chats:
        store.delete([rng.choice(chats)])


def _acknowledge_and_delete(
    store: EventStore, tracker: MailboxPendingTracker, rng: random.Random
) -> None:
    chats = [event.seq for event in store.read_since(0) if event.kind == EventKind.CHAT]
    if not chats:
        return
    tracker.acknowledge(rng.choice(_SENDERS), rng.choice(chats), roles=("P/reviewer",))
    store.delete([rng.choice(chats)])


@pytest.mark.skipif(not sqlcipher_available(), reason="sqlcipher3-binary not installed")
def test_conversion_copies_a_finished_layout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    plain = tmp_path / "plain.db"
    encrypted = tmp_path / "enc.db"
    key = generate_key_file(tmp_path / "hub.key")
    source = EventStore(plain)
    _chat(source, target="PROJ/BOB")
    MailboxPendingTracker(source)
    copied = source.storage.connection.execute(
        "SELECT COUNT(*) FROM mailbox_address_hits"
    ).fetchone()
    source.close()
    assert migrate_plaintext_to_sqlcipher(plain, encrypted, key_file=key)["rows"] == 1
    opened = EventStore(encrypted, key_file=key)
    try:
        assert layout_version(opened.storage.connection) == 1
        caplog.set_level(logging.INFO, logger="synapse.hub")
        tracker = MailboxPendingTracker(opened)
        assert "mailbox pending layout walk started" not in caplog.text
        assert tracker.pending_count("PROJ/BOB") == 1
        assert copied is not None and copied[0] == 1
    finally:
        opened.close()


@pytest.mark.skipif(not sqlcipher_available(), reason="sqlcipher3-binary not installed")
def test_conversion_leaves_an_unprojected_store_to_walk(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    plain = tmp_path / "plain.db"
    encrypted = tmp_path / "enc.db"
    key = generate_key_file(tmp_path / "hub.key")
    source = EventStore(plain)
    _chat(source, target="PROJ/BOB,PROJ")
    source.close()
    migrate_plaintext_to_sqlcipher(plain, encrypted, key_file=key)
    opened = EventStore(encrypted, key_file=key)
    try:
        assert layout_version(opened.storage.connection) == 0
        caplog.set_level(logging.INFO, logger="synapse.hub")
        tracker = MailboxPendingTracker(opened)
        assert "mailbox pending layout walk started" in caplog.text
        assert tracker.pending_count("PROJ/BOB") == 1
        assert layout_version(opened.storage.connection) == 1
    finally:
        opened.close()


async def test_a_cold_count_answers_when_the_chat_journal_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cold mailbox still answers after the chat journal refuses further reads.

    The startup walk may read the journal. ``P/ann`` is absent from that
    projection: only the bare project was addressed. Once the tracker exists,
    a cold snapshot, a cold pending count, and a who request must not read it.
    """
    store = EventStore(tmp_path / "hub.db")
    hub: SynapseHub | None = None
    try:
        _chat(store, sender="PEER", target="P")
        _chat(store, sender="PEER", target="P")
        hub = SynapseHub(journal=store, anti_rollback_checkpoint=False, hub_id="cold-count")
        assert "P/ann" not in hub.mailbox_pending.known_identities

        def _refuse_journal_read(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("chat journal read")

        monkeypatch.setattr(EventStore, "read_window", _refuse_journal_read)
        monkeypatch.setattr(EventStore, "iter_events", _refuse_journal_read)
        tracker = hub.mailbox_pending
        counts = tracker.snapshot(("P/ann",), lambda _name: ())
        assert counts is not None
        assert counts["P/ann"] == 2
        assert counts["P"] == 2
        assert tracker.pending_count("P/ann") == 2
        async with running_hub(hub) as (_serving, uri):
            async with connect(uri) as websocket:
                await read_until_type(websocket, "welcome")
                await websocket.send(json.dumps({"sender": "SEAT", "type": "who_request"}))
                who = await read_until_type(websocket, "who_snapshot")
        assert who["mailbox_pending"]["P/ann"] == 2
        assert who["mailbox_pending"]["P"] == 2
    finally:
        if hub is not None:
            hub.close()
        store.close()
