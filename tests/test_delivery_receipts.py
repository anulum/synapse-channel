# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable delivery-receipt projection regressions

from __future__ import annotations

from synapse_channel.core.delivery_receipts import (
    deferred_receipt_payload,
    expired_receipt_payload,
    format_receipt_event,
    immediate_receipt_payload,
    receipt_event_matches,
    receipt_event_to_json,
    requested_receipt_payload,
    restore_pending_receipts,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.pending_receipts import ReceiptEntry
from synapse_channel.core.persistence import StoredEvent


def _event(seq: int, kind: str, payload: dict[str, object]) -> StoredEvent:
    """Return one stored event for read-side projection tests."""
    return StoredEvent(seq=seq, ts=float(seq), kind=kind, payload=payload)


def test_restore_pending_receipts_keeps_unsettled_immediate_failures() -> None:
    events = (
        _event(
            1,
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            immediate_receipt_payload(
                sender="ALICE",
                target="BOB",
                message_id=5,
                message_seq=10,
                delivered=False,
                recipients=(),
            ),
        ),
        _event(
            2,
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            immediate_receipt_payload(
                sender="CAROL",
                target="DAVE",
                message_id=6,
                message_seq=11,
                delivered=False,
                recipients=(),
            ),
        ),
        _event(
            3,
            EventKind.DELIVERY_RECEIPT_DEFERRED,
            deferred_receipt_payload(
                entry=ReceiptEntry(sender="CAROL", target="DAVE", message_id=6),
                message_seq=11,
                recipient="DAVE",
            ),
        ),
    )

    assert restore_pending_receipts(events) == (
        (10, ReceiptEntry(sender="ALICE", target="BOB", message_id=5)),
    )


def test_restore_pending_receipts_drops_expired_entries() -> None:
    entry = ReceiptEntry(sender="ALICE", target="BOB", message_id=5)
    events = (
        _event(
            1,
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            immediate_receipt_payload(
                sender=entry.sender,
                target=entry.target,
                message_id=entry.message_id,
                message_seq=10,
                delivered=False,
                recipients=(),
            ),
        ),
        _event(
            2,
            EventKind.DELIVERY_RECEIPT_EXPIRED,
            expired_receipt_payload(entry=entry, message_seq=10, reason="pending_window_evicted"),
        ),
    )

    assert restore_pending_receipts(events) == ()


def test_restore_pending_receipts_ignores_malformed_payloads() -> None:
    events = (
        _event(
            1,
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            {
                "sender": 123,
                "target": "BOB",
                "message_id": True,
                "message_seq": 10,
                "delivered": False,
            },
        ),
        _event(
            2,
            EventKind.DELIVERY_RECEIPT_DEFERRED,
            {"message_seq": "not-an-int"},
        ),
        _event(
            3,
            EventKind.DELIVERY_RECEIPT_REQUESTED,
            requested_receipt_payload(sender="ALICE", target="BOB", message_id=1, message_seq=4),
        ),
    )

    assert restore_pending_receipts(events) == ()


def test_receipt_event_matching_and_json_are_stable() -> None:
    event = _event(
        7,
        EventKind.DELIVERY_RECEIPT_REQUESTED,
        requested_receipt_payload(sender="ALICE", target="BOB", message_id=5, message_seq=10),
    )

    assert receipt_event_matches(event, "ALICE")
    assert receipt_event_matches(event, "BOB")
    assert receipt_event_matches(event, "all")
    assert not receipt_event_matches(event, "MALLORY")
    payload = receipt_event_to_json(event)
    assert payload["phase"] == "requested"
    assert payload["message_seq"] == 10
    assert "phase=requested" in format_receipt_event(event)


def test_receipt_event_matching_handles_all_recipients_and_non_receipts() -> None:
    event = _event(
        8,
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        immediate_receipt_payload(
            sender="ALICE",
            target="BOB",
            message_id=5,
            message_seq=10,
            delivered=True,
            recipients=("BOB",),
        ),
    )
    non_receipt = _event(9, EventKind.CHAT, {"sender": "ALICE", "target": "BOB"})

    assert receipt_event_matches(event, "")
    assert receipt_event_matches(event, "all")
    assert receipt_event_matches(event, "BOB")
    assert not receipt_event_matches(non_receipt, "all")
    assert "recipients=BOB" in format_receipt_event(event)
    assert "delivered=True" in format_receipt_event(event)


def test_receipt_json_handles_non_list_recipients_and_non_prefixed_kinds() -> None:
    event = _event(
        10,
        "custom_kind",
        {
            "sender": "ALICE",
            "target": "BOB",
            "message_id": "bad",
            "message_seq": True,
            "recipients": "BOB",
            "reason": "manual",
        },
    )

    payload = receipt_event_to_json(event)

    assert payload["phase"] == "custom_kind"
    assert payload["message_id"] is None
    assert payload["message_seq"] is None
    assert payload["recipients"] == []
    assert "reason=manual" in format_receipt_event(event)
