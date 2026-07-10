# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable mailbox pending projection tests

from __future__ import annotations

from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_pending import (
    MailboxPendingTracker,
    format_pending_line,
    parse_pending_counts,
)
from synapse_channel.core.persistence import EventStore


def _chat(
    store: EventStore,
    *,
    sender: str = "PEER",
    target: str = "PROJ/BOB",
    channel: str = "",
) -> tuple[int, dict[str, Any]]:
    payload: dict[str, Any] = {
        "sender": sender,
        "target": target,
        "type": "chat",
        "payload": f"to {target}",
    }
    if channel:
        payload["channel"] = channel
    return store.append(EventKind.CHAT, payload), payload


def test_pending_count_wire_parser_and_sentence_are_fail_visible() -> None:
    assert parse_pending_counts(None) is None
    assert parse_pending_counts([]) is None
    assert parse_pending_counts(
        {
            "A": 2,
            "B": 0,
            "": 4,
            "negative": -1,
            "boolean": True,
            "text": "3",
            7: 3,
        }
    ) == {"A": 2, "B": 0}
    assert format_pending_line("A", 1) == "1 undelivered message pending for A"
    assert format_pending_line("A", 2) == "2 undelivered messages pending for A"


def test_no_journal_reports_unavailable_and_never_mutates() -> None:
    tracker = MailboxPendingTracker(None)

    tracker.observe_chat(1, {"sender": "A", "target": "B"})

    assert tracker.available is False
    assert tracker.snapshot(("B",), lambda _name: ()) is None
    assert tracker.advance("B", 1, source="cursor") is False
    assert tracker.acknowledge("B", 1) is False
    assert tracker.known_identities == ()


def test_projection_matches_mailbox_replay_routing(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        _chat(store, target="PROJ/BOB")
        _chat(store, target="PROJ")
        _chat(store, target="PROJ/BOB,OTHER")
        _chat(store, target="all")
        _chat(store, target="PROJ/*")
        _chat(store, target="PROJ/BOB", channel="private")
        _chat(store, sender="PROJ/BOB", target="PROJ/BOB")
        tracker = MailboxPendingTracker(store)

        counts = tracker.snapshot(("PROJ/BOB",), lambda _name: ())
    finally:
        store.close()

    assert counts is not None
    assert counts["PROJ/BOB"] == 3


def test_role_change_recomputes_only_that_identity(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        _chat(store, target="PROJ/reviewer")
        tracker = MailboxPendingTracker(store)
        without_role = tracker.snapshot(("PROJ/BOB",), lambda _name: ())
        with_role = tracker.snapshot(
            ("PROJ/BOB",),
            lambda _name: ("PROJ/reviewer",),
        )
    finally:
        store.close()

    assert without_role is not None and without_role["PROJ/BOB"] == 0
    assert with_role is not None and with_role["PROJ/BOB"] == 1


def test_logical_identity_and_sidecar_roles_are_combined(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        _chat(store, target="PROJ/reviewer")
        _chat(store, target="PROJ/triage")
        tracker = MailboxPendingTracker(store)

        counts = tracker.snapshot(
            ("PROJ/BOB", "PROJ/BOB-rx"),
            lambda name: {
                "PROJ/BOB": ("PROJ/reviewer",),
                "PROJ/BOB-rx": ("PROJ/triage",),
            }.get(name, ()),
        )
    finally:
        store.close()

    assert counts is not None and counts["PROJ/BOB"] == 2


def test_cursor_advance_is_monotonic_capped_and_journalled(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        first, _ = _chat(store)
        _second, _ = _chat(store)
        tracker = MailboxPendingTracker(store)
        assert tracker.snapshot(("PROJ/BOB",), lambda _name: ()) == {"PROJ/BOB": 2}

        assert tracker.advance("PROJ/BOB", first, source="cursor") is True
        assert tracker.snapshot(("PROJ/BOB",), lambda _name: ()) == {"PROJ/BOB": 1}
        assert tracker.advance("PROJ/BOB", first, source="cursor") is False
        assert tracker.advance("PROJ/BOB", 999_999, source="cursor") is True
        assert tracker.snapshot(("PROJ/BOB",), lambda _name: ()) == {"PROJ/BOB": 0}

        watermarks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
    finally:
        store.close()

    assert [event.payload["source"] for event in watermarks] == ["cursor", "cursor"]
    assert tracker.watermark_for("PROJ/BOB") == watermarks[-1].payload["through_seq"]


def test_ack_validates_the_stored_chat_and_logical_recipient(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        exact, _ = _chat(store)
        broadcast, _ = _chat(store, target="all")
        role, _ = _chat(store, target="PROJ/reviewer")
        tracker = MailboxPendingTracker(store)

        assert tracker.acknowledge("OTHER", exact) is False
        assert tracker.acknowledge("PROJ/BOB", broadcast) is False
        assert tracker.acknowledge("PROJ/BOB", True) is False
        assert tracker.acknowledge("PROJ/BOB", 999_999) is False
        assert tracker.acknowledge("PROJ/BOB", role, roles=("PROJ/reviewer",)) is True
    finally:
        store.close()

    assert tracker.watermark_for("PROJ/BOB") == role


def test_watermark_and_pending_count_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    first, _ = _chat(store)
    _chat(store)
    tracker = MailboxPendingTracker(store)
    assert tracker.advance("PROJ/BOB", first, source="cursor") is True
    store.close()

    reopened = EventStore(path)
    try:
        restored = MailboxPendingTracker(reopened)
        counts = restored.snapshot(("PROJ/BOB",), lambda _name: ())
    finally:
        reopened.close()

    assert restored.watermark_for("PROJ/BOB") == first
    assert counts == {"PROJ/BOB": 1}


def test_new_live_chat_increments_a_materialised_count(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        tracker = MailboxPendingTracker(store)
        assert tracker.snapshot(("PROJ/BOB-rx",), lambda _name: ()) == {"PROJ/BOB": 0}
        seq, payload = _chat(store)

        tracker.observe_chat(seq, payload)
        counts = tracker.snapshot(("PROJ/BOB-rx",), lambda _name: ())
    finally:
        store.close()

    assert counts == {"PROJ/BOB": 1}


def test_identity_retention_is_bounded_lru(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        tracker = MailboxPendingTracker(store, max_identities=2)
        for identity in ("A", "B", "C"):
            seq, payload = _chat(store, target=identity)
            tracker.observe_chat(seq, payload)
    finally:
        store.close()

    assert tracker.known_identities == ("B", "C")


def test_empty_identity_and_zero_cursor_do_not_create_watermarks(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        tracker = MailboxPendingTracker(store)

        assert tracker.advance("  ", 1, source="cursor") is False
        assert tracker.acknowledge("  ", 1) is False
        assert tracker.advance("A", 0, source="cursor") is False
        assert tracker.watermark_for("A") == 0
        assert store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,)) == []
    finally:
        store.close()


def test_restore_and_live_observer_ignore_malformed_or_irrelevant_rows(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        seq, payload = _chat(store, target="A")
        store.append(
            EventKind.MAILBOX_WATERMARK,
            {"identity": "", "through_seq": seq},
        )
        tracker = MailboxPendingTracker(store)
        before = tracker.snapshot(("A", ""), lambda _name: ())
        assert before is not None and before["A"] == 1
        assert tracker.advance("A", seq, source="cursor") is True

        tracker.observe_chat(seq, payload)
        tracker.observe_chat(seq + 100, {"sender": "PEER", "target": "all"})
        after = tracker.snapshot(("A",), lambda _name: ())
        assert after is not None and after["A"] == 0
    finally:
        store.close()
