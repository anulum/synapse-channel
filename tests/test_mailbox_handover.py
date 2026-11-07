# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a mailbox handover is validated, authorised, bounded and journalled once
"""The request, the authorisation rule, the pending selection and the journal record."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_handover import (
    ACTION_CLOSE,
    ACTION_HANDOVER,
    MAX_REASON_LENGTH,
    REFUSED_NOT_AUTHORISED,
    HandoverLedger,
    HandoverRequest,
    MailboxHandoverError,
    authorise_handover,
    decode_handover_request,
    forward_frame,
    record_mailbox_handover,
)
from synapse_channel.core.mailbox_pending import MailboxPendingTracker
from synapse_channel.core.persistence import EventStore, StoredEvent


def _chat(store: EventStore, sender: str, target: str, text: str, **extra: Any) -> int:
    payload = {"sender": sender, "target": target, "payload": text, "timestamp": 5.0, **extra}
    return store.append(EventKind.CHAT, payload)


def test_a_request_with_a_successor_is_a_handover() -> None:
    request = decode_handover_request(
        {"from": " P/old ", "to": "P/new", "through_seq": 9, "reason": "  session ended "}
    )
    assert request == HandoverRequest("P/old", "P/new", 9, "session ended")
    assert request.action == ACTION_HANDOVER
    assert request.watermark_source == "handover"


def test_a_request_without_a_successor_is_a_close() -> None:
    request = decode_handover_request({"from": "P/old", "reason": "nobody reads this"})
    assert request.successor is None and request.through_seq is None
    assert request.action == ACTION_CLOSE
    assert request.watermark_source == "closed"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"reason": "r"}, "from must be"),
        ({"from": " ", "reason": "r"}, "from must be"),
        ({"from": 3, "reason": "r"}, "from must be"),
        ({"from": "P/a@HUB", "reason": "r"}, "another hub"),
        ({"from": "P/*", "reason": "r"}, "not one mailbox"),
        ({"from": "P/a,P/b", "reason": "r"}, "not one mailbox"),
        ({"from": "all", "reason": "r"}, "not one mailbox"),
        ({"from": "P/a", "to": "", "reason": "r"}, "to must be"),
        ({"from": "P/a", "to": "P/a", "reason": "r"}, "differ"),
        ({"from": "P/a", "through_seq": 0, "reason": "r"}, "positive"),
        ({"from": "P/a", "through_seq": True, "reason": "r"}, "positive"),
        ({"from": "P/a", "through_seq": "9", "reason": "r"}, "positive"),
        ({"from": "P/a"}, "reason"),
        ({"from": "P/a", "reason": "   "}, "reason"),
        ({"from": "P/a", "reason": "x" * (MAX_REASON_LENGTH + 1)}, "at most"),
    ],
)
def test_a_malformed_request_is_refused(data: dict[str, Any], message: str) -> None:
    with pytest.raises(MailboxHandoverError, match=message):
        decode_handover_request(data)


def test_the_name_its_custodian_and_a_granted_operator_may_act() -> None:
    assert authorise_handover("P/old", "P/old", custodian=None, operator_allowed=False) is None
    assert authorise_handover("P/boss", "P/old", custodian="P/boss", operator_allowed=False) is None
    assert authorise_handover("OPS/x", "P/old", custodian=None, operator_allowed=True) is None


def test_anyone_else_is_refused() -> None:
    refused = authorise_handover("P/peer", "P/old", custodian="P/boss", operator_allowed=False)
    assert refused == REFUSED_NOT_AUTHORISED
    assert authorise_handover("P/old-rx", "P/old", custodian=None, operator_allowed=False)


def test_pending_chats_follow_the_count_rule_and_stop_at_the_limit(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        first = _chat(store, "X/z", "P/old", "one")
        _chat(store, "X/z", "P/other", "not for old")
        _chat(store, "P/old", "P/old", "own message")
        _chat(store, "X/z", "all", "broadcast")
        _chat(store, "X/z", "P/old", "channel", channel="ops")
        second = _chat(store, "X/z", "P/old,P/other", "two")
        third = _chat(store, "X/z", "P/old", "three")
        tracker = MailboxPendingTracker(store)
        chats, covered = tracker.pending_chats("P/old", through_seq=99, limit=10, page=2)
        assert [event.seq for event in chats] == [first, second, third]
        assert covered == store.max_seq()
        chats, covered = tracker.pending_chats("P/old", through_seq=99, limit=2, page=2)
        assert [event.seq for event in chats] == [first, second]
        assert covered == second


def test_pending_chats_start_after_the_watermark_and_end_at_through_seq(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        first = _chat(store, "X/z", "P/old", "one")
        second = _chat(store, "X/z", "P/old", "two")
        _chat(store, "X/z", "P/old", "three")
        tracker = MailboxPendingTracker(store)
        assert tracker.advance("P/old", first, source="ack")
        chats, covered = tracker.pending_chats("P/old", through_seq=second, limit=10)
        assert [event.seq for event in chats] == [second]
        assert covered == second
        chats, covered = tracker.pending_chats("P/old", through_seq=first, limit=10)
        assert (chats, covered) == ((), first)


def test_trailing_events_that_are_not_chats_end_the_scan(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        seq = _chat(store, "X/z", "P/old", "one")
        store.append(EventKind.CLAIM, {"task_id": "T"})
        tracker = MailboxPendingTracker(store)
        chats, covered = tracker.pending_chats("P/old", through_seq=99, limit=5)
        assert [event.seq for event in chats] == [seq]
        assert covered == store.max_seq()


def test_pending_chats_without_a_store_are_empty() -> None:
    assert MailboxPendingTracker(None).pending_chats("P/old", through_seq=5, limit=1) == ((), 0)


def test_a_role_chat_counts_for_a_name_that_held_the_role(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hub.db") as store:
        seq = _chat(store, "X/z", "P/manager", "for the role")
        tracker = MailboxPendingTracker(store)
        tracker.snapshot(["P/old"], lambda _name: ["P/manager"])
        chats, _covered = tracker.pending_chats("P/old", through_seq=seq, limit=5)
        assert [event.seq for event in chats] == [seq]


def test_the_record_is_durable_and_the_ledger_restores_its_key(tmp_path: Path) -> None:
    database = tmp_path / "hub.db"
    request = HandoverRequest("P/old", "P/new", None, "ended")
    with EventStore(database) as store:
        assert ("P/old", 7) not in HandoverLedger(store)
        seq = record_mailbox_handover(store, request, actor="P/boss", through_seq=7, forwarded=[3])
        store.append(EventKind.MAILBOX_HANDOVER, {"from": "P/bad", "through_seq": "7"})
        store.append(EventKind.MAILBOX_HANDOVER, {"from": "P/bad", "through_seq": True})
        stored = store.read_window(min_seq=seq, max_seq=seq)[0]
    assert stored.payload == {
        "action": "handover",
        "from": "P/old",
        "to": "P/new",
        "through_seq": 7,
        "reason": "ended",
        "actor": "P/boss",
        "forwarded": [3],
    }
    with EventStore(database) as store:
        ledger = HandoverLedger(store)
    assert ("P/old", 7) in ledger
    assert ("P/bad", 7) not in ledger
    ledger.add("P/old", 9)
    assert ("P/old", 9) in ledger
    assert ("P/old", 9) not in HandoverLedger(None)


def test_a_forward_cites_the_original_chat() -> None:
    event = StoredEvent(
        12,
        4.0,
        EventKind.CHAT,
        {"sender": "X/z", "target": "P/old", "payload": "body", "timestamp": 4.0},
    )
    assert forward_frame(event, source="P/old", successor="P/new") == {
        "type": "chat",
        "target": "P/new",
        "payload": "body",
        "handover_of": 12,
        "handover_from": "P/old",
        "original_sender": "X/z",
        "original_target": "P/old",
        "original_timestamp": 4.0,
    }
    bare = StoredEvent(13, 4.0, EventKind.CHAT, {})
    frame = forward_frame(bare, source="P/old", successor="P/new")
    assert (frame["payload"], frame["original_sender"], frame["original_target"]) == ("", "", "")
