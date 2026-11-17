# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a restarted tracker keeps the mailboxes the journal scan kept, in its order

from __future__ import annotations

import logging
import random
import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.journal import EventKind, record_mailbox_watermark, record_role_binding
from synapse_channel.core.mailbox_pending import MailboxPendingTracker
from synapse_channel.core.mailbox_pending_layout import copy_mailbox_layout
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.role_mailbox import mailbox_role

_TARGETS = (
    "P/ann",
    "P/bob",
    "Q/dee",
    "P",
    "P/reviewer",
    "P/r2",
    "role:P/reviewer",
    "ROLE:P/x",
    "P/ann,P/ann",
    "P/bob,P/ann",
    "P/reviewer,role:P/reviewer",
    "P/a?n",
    "P/*",
    "P/ann,Q/*",
    "all",
    "seat@OTHER",
    "P/ann,seat@OTHER",
    ",",
    "",
)
_SENDERS = ("P/ann", "P/bob", "Q/dee", "PEER")
_IDENTITIES = ("P/ann", "P/bob", "Q/dee", "P", "P/reviewer", "role:P/reviewer", "role:P/r2")
_ROLES = ("P/reviewer", "P/r2")
_BOUNDS = (1, 2, 3, 512)


class _ScanRestoreTracker(MailboxPendingTracker):
    """The tracker with its restore copied verbatim from ``36e90a05``.

    That commit is the canonical main before mailbox counts were stored. Its
    restore read every chat, watermark and role binding of the journal in order.
    """

    def _restore(self) -> None:
        """Restore known mailboxes and the highest valid watermark of each."""
        if self.store is None:
            return
        kinds = (EventKind.CHAT, EventKind.MAILBOX_WATERMARK, EventKind.ROLE_BINDING)
        for event in self.store.iter_events(kinds=kinds):
            if event.kind == EventKind.ROLE_BINDING:
                self._restore_binding(event.payload)
                continue
            if event.kind == EventKind.CHAT:
                for identity in self._target_identities(str(event.payload.get("target") or "all")):
                    self._remember(identity)
                continue
            identity = str(event.payload.get("identity") or "").strip()
            if not identity:
                continue
            through_seq = safe_int(
                event.payload.get("through_seq"),
                default=0,
                min_value=0,
                allow_bool=False,
            )
            role = mailbox_role(identity)
            carried = 0 if role is None else self._adopt_plain_name(role)
            self._remember(identity)
            if identity not in self._known:
                continue
            self._watermarks[identity] = max(
                self.watermark_for(identity),
                carried,
                min(through_seq, event.seq),
            )


def _chat(store: EventStore, target: str, *, sender: str = "PEER", channel: bool = False) -> int:
    payload: dict[str, object] = {"sender": sender, "type": "chat", "payload": "x"}
    if target:
        payload["target"] = target
    if channel:
        payload["channel"] = "ops"
    return store.append(EventKind.CHAT, payload)


def _random_event(store: EventStore, rng: random.Random) -> None:
    roll = rng.random()
    chats = [event.seq for event in store.read_since(0) if event.kind == EventKind.CHAT]
    if roll < 0.08:
        identity = rng.choice(_SENDERS)
        roles = tuple(sorted(rng.sample(_ROLES, rng.randint(0, len(_ROLES)))))
        record_role_binding(store, identity=identity, roles=roles)
    elif roll < 0.18 and chats:
        record_mailbox_watermark(
            store,
            identity=rng.choice(_IDENTITIES),
            through_seq=rng.choice(chats),
            source="cursor",
        )
    elif roll < 0.23 and chats:
        store.delete([rng.choice(chats)])
    else:
        _chat(
            store,
            rng.choice(_TARGETS),
            sender=rng.choice(_SENDERS),
            channel=rng.random() < 0.15,
        )


def _assert_restores_match(path: Path) -> None:
    for bound in _BOUNDS:
        store = EventStore(path)
        try:
            restored = MailboxPendingTracker(store, max_identities=bound).known_identities
            scanned = _ScanRestoreTracker(store, max_identities=bound).known_identities
        finally:
            store.close()
        assert restored == scanned, (bound, restored, scanned)


@pytest.mark.parametrize("seed", range(12))
def test_a_restart_keeps_the_scan_s_mailboxes_in_its_order(tmp_path: Path, seed: int) -> None:
    rng = random.Random(20261116 + seed)
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        for _ in range(rng.randint(5, 30)):
            _random_event(store, rng)
        if seed % 2:
            MailboxPendingTracker(store)
        for _ in range(rng.randint(5, 30)):
            _random_event(store, rng)
    finally:
        store.close()
    _assert_restores_match(path)


def test_the_order_follows_the_last_mention(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        for target in ("P/a", "P/b", "P/c", "P/a"):
            _chat(store, target)
    finally:
        store.close()
    store = EventStore(path)
    try:
        assert MailboxPendingTracker(store, max_identities=2).known_identities == ("P/c", "P/a")
    finally:
        store.close()


def test_a_name_from_a_live_chat_or_a_channel_chat_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        _chat(store, "P/a")
        tracker = MailboxPendingTracker(store)
        seq = _chat(store, "P/d")
        tracker.observe_chat(seq, {"sender": "PEER", "target": "P/d"})
        _chat(store, "P/e", channel=True)
    finally:
        store.close()
    store = EventStore(path)
    try:
        assert MailboxPendingTracker(store).known_identities == ("P/a", "P/d", "P/e")
    finally:
        store.close()


def test_a_deleted_last_mention_moves_the_name_back(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        _chat(store, "P/a")
        _chat(store, "P/b")
        MailboxPendingTracker(store)
        last = _chat(store, "P/a")
        only = _chat(store, "P/c")
        assert store.delete([last, only]) == 2
    finally:
        store.close()
    store = EventStore(path)
    try:
        assert MailboxPendingTracker(store).known_identities == ("P/a", "P/b")
    finally:
        store.close()
    _assert_restores_match(path)


def _chat_written_like_a_build_without_mentions(store: EventStore, target: str) -> int:
    """Store one chat as a build that keeps hits and the extent, but no mentions, did."""
    connection = store.storage.connection
    cursor = connection.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (1.0, 'chat', ?)",
        (f'{{"sender": "PEER", "target": "{target}", "type": "chat"}}',),
    )
    seq = int(cursor.lastrowid or 0)
    connection.execute(
        "INSERT INTO mailbox_address_hits (seq, address, sender) VALUES (?, ?, 'PEER')",
        (seq, target),
    )
    connection.execute(
        "UPDATE mailbox_pending_layout SET projected_through = ?, chat_rows = chat_rows + 1",
        (seq,),
    )
    connection.commit()
    return seq


def _reopened_with_bound(
    path: Path, bound: int, caplog: pytest.LogCaptureFixture
) -> tuple[str, ...]:
    caplog.set_level(logging.INFO, logger="synapse.hub")
    caplog.clear()
    store = EventStore(path)
    try:
        known = MailboxPendingTracker(store, max_identities=bound).known_identities
        assert "mailbox name mentions are behind the journal" in caplog.text
        caplog.clear()
        MailboxPendingTracker(store)
        assert "walk started" not in caplog.text
        return known
    finally:
        store.close()


def test_a_chat_stored_without_its_mentions_is_walked_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        _chat(store, "P/a")
        _chat(store, "P/b")
        MailboxPendingTracker(store)
        _chat_written_like_a_build_without_mentions(store, "P/a")
    finally:
        store.close()
    assert _reopened_with_bound(path, 1, caplog) == ("P/a",)
    _assert_restores_match(path)


def test_a_later_append_keeps_that_gap_visible(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        _chat(store, "P/a")
        _chat(store, "P/b")
        MailboxPendingTracker(store)
        _chat_written_like_a_build_without_mentions(store, "P/a")
        _chat(store, "P/c")
    finally:
        store.close()
    assert _reopened_with_bound(path, 2, caplog) == ("P/a", "P/c")
    _assert_restores_match(path)


def test_a_later_delete_keeps_that_gap_visible(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        _chat(store, "P/a")
        _chat(store, "P/b")
        doomed = _chat(store, "P/x")
        MailboxPendingTracker(store)
        _chat_written_like_a_build_without_mentions(store, "P/a")
        assert store.delete([doomed]) == 1
    finally:
        store.close()
    assert _reopened_with_bound(path, 2, caplog) == ("P/b", "P/a")
    _assert_restores_match(path)


def test_a_layout_written_before_mentions_were_kept_is_walked_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        _chat(store, "P/b")
        _chat(store, "P/a")
        MailboxPendingTracker(store)
        connection = store.storage.connection
        connection.execute("DROP TABLE mailbox_name_mentions")
        connection.execute("ALTER TABLE mailbox_pending_layout DROP COLUMN mentions_through")
        connection.commit()
    finally:
        store.close()
    caplog.set_level(logging.INFO, logger="synapse.hub")
    store = EventStore(path)
    try:
        assert MailboxPendingTracker(store).known_identities == ("P/b", "P/a")
        assert "walk started" in caplog.text
        caplog.clear()
        MailboxPendingTracker(store)
        assert "walk started" not in caplog.text
    finally:
        store.close()


def test_a_copied_layout_keeps_its_mentions(tmp_path: Path) -> None:
    source_path = tmp_path / "source.db"
    store = EventStore(source_path)
    try:
        for name in ("P/a", "P/b", "P/a"):
            _chat(store, name)
        MailboxPendingTracker(store)
    finally:
        store.close()
    destination_path = tmp_path / "destination.db"
    destination = EventStore(destination_path)
    destination.close()
    source = sqlite3.connect(source_path)
    converted = sqlite3.connect(destination_path)
    try:
        converted.execute("DELETE FROM events")
        converted.executemany(
            "INSERT INTO events (seq, ts, kind, payload) VALUES (?, ?, ?, ?)",
            source.execute("SELECT seq, ts, kind, payload FROM events ORDER BY seq").fetchall(),
        )
        assert copy_mailbox_layout(source, converted) is True
        converted.commit()
        copied = converted.execute(
            "SELECT seq, position, name FROM mailbox_name_mentions ORDER BY seq, position"
        ).fetchall()
    finally:
        source.close()
        converted.close()
    assert copied == [(1, 0, "P/a"), (2, 0, "P/b"), (3, 0, "P/a")]
    store = EventStore(destination_path)
    try:
        assert MailboxPendingTracker(store, max_identities=1).known_identities == ("P/a",)
    finally:
        store.close()


def test_a_copy_from_a_layout_without_mentions_walks_on_first_open(tmp_path: Path) -> None:
    source_path = tmp_path / "source.db"
    store = EventStore(source_path)
    try:
        _chat(store, "P/b")
        _chat(store, "P/a")
        MailboxPendingTracker(store)
        connection = store.storage.connection
        connection.execute("DROP TABLE mailbox_name_mentions")
        connection.execute("ALTER TABLE mailbox_pending_layout DROP COLUMN mentions_through")
        connection.commit()
    finally:
        store.close()
    destination_path = tmp_path / "destination.db"
    EventStore(destination_path).close()
    source = sqlite3.connect(source_path)
    converted = sqlite3.connect(destination_path)
    try:
        converted.executemany(
            "INSERT INTO events (seq, ts, kind, payload) VALUES (?, ?, ?, ?)",
            source.execute("SELECT seq, ts, kind, payload FROM events ORDER BY seq").fetchall(),
        )
        assert copy_mailbox_layout(source, converted) is True
        converted.commit()
    finally:
        source.close()
        converted.close()
    store = EventStore(destination_path)
    try:
        assert MailboxPendingTracker(store).known_identities == ("P/b", "P/a")
    finally:
        store.close()
    _assert_restores_match(destination_path)


def test_a_copy_from_stale_mentions_walks_on_first_open(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source_path = tmp_path / "source.db"
    store = EventStore(source_path)
    try:
        _chat(store, "P/a")
        _chat(store, "P/b")
        MailboxPendingTracker(store)
        _chat_written_like_a_build_without_mentions(store, "P/a")
    finally:
        store.close()
    destination_path = tmp_path / "destination.db"
    EventStore(destination_path).close()
    source = sqlite3.connect(source_path)
    converted = sqlite3.connect(destination_path)
    try:
        converted.executemany(
            "INSERT INTO events (seq, ts, kind, payload) VALUES (?, ?, ?, ?)",
            source.execute("SELECT seq, ts, kind, payload FROM events ORDER BY seq").fetchall(),
        )
        assert copy_mailbox_layout(source, converted) is True
        converted.commit()
        assert converted.execute(
            "SELECT projected_through, mentions_through FROM mailbox_pending_layout"
        ).fetchall() == [(3, -1)]
    finally:
        source.close()
        converted.close()
    assert _reopened_with_bound(destination_path, 1, caplog) == ("P/a",)
    _assert_restores_match(destination_path)
