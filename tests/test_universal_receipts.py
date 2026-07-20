# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — universal receipt projection regressions

from __future__ import annotations

from synapse_channel.core.approvals import format_approval_note
from synapse_channel.core.delivery_receipts import (
    deferred_receipt_payload,
    expired_receipt_payload,
    immediate_receipt_payload,
    requested_receipt_payload,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.pending_receipts import ReceiptEntry
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.universal_receipts import (
    UniversalReceipt,
    format_universal_receipt,
    universal_receipt_from_event,
    universal_receipt_matches,
    universal_receipt_to_json,
    universal_receipts_from_events,
)


def _event(seq: int, kind: str, payload: dict[str, object]) -> StoredEvent:
    """Return one stored event with a deterministic timestamp."""
    return StoredEvent(seq=seq, ts=float(seq), kind=kind, payload=payload)


def test_delivery_receipts_project_requested_immediate_and_deferred_phases() -> None:
    events = (
        _event(
            1,
            EventKind.DELIVERY_RECEIPT_REQUESTED,
            requested_receipt_payload(sender="ALICE", target="BOB", message_id=7, message_seq=10),
        ),
        _event(
            2,
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            immediate_receipt_payload(
                sender="ALICE",
                target="BOB",
                message_id=7,
                message_seq=10,
                delivered=False,
                recipients=(),
            ),
        ),
        _event(
            3,
            EventKind.DELIVERY_RECEIPT_DEFERRED,
            deferred_receipt_payload(
                entry=ReceiptEntry(sender="ALICE", target="BOB", message_id=7),
                message_seq=10,
                recipient="BOB",
            ),
        ),
    )

    receipts = universal_receipts_from_events(events)

    assert [receipt.status for receipt in receipts] == ["requested", "undelivered", "delivered"]
    assert {receipt.kind for receipt in receipts} == {"delivery"}
    assert receipts[2].subject == "BOB"
    assert universal_receipt_matches(receipts[2], "BOB")
    assert "kind=delivery" in format_universal_receipt(receipts[2])


def test_delivery_receipts_project_expiry_and_fallback_phases() -> None:
    expired = _event(
        14,
        EventKind.DELIVERY_RECEIPT_EXPIRED,
        expired_receipt_payload(
            entry=ReceiptEntry(sender="ALICE", target="BOB", message_id=8),
            message_seq=11,
            reason="bounded-window",
        ),
    )
    immediate_without_verdict = _event(
        15,
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        {"sender": "ALICE", "target": "TEAM", "message_id": 9, "recipients": ["worker-rx"]},
    )

    receipts = universal_receipts_from_events((expired, immediate_without_verdict))

    assert [receipt.status for receipt in receipts] == ["expired", "immediate"]
    assert "message_seq=11" in receipts[0].summary
    assert "message_seq=" not in receipts[1].summary
    assert universal_receipt_matches(receipts[1], "worker-rx")


def test_sandbox_operator_and_cross_hub_events_project_to_the_shared_shape() -> None:
    sandbox = _event(
        4,
        EventKind.SANDBOX_RUN,
        {"tool_id": "lint-tool", "exit": "ok", "fuel_used": 12, "reason": ""},
    )
    operator = _event(
        5,
        EventKind.OPERATOR_RELAY,
        {
            "action": "release",
            "task_id": "TASK-1",
            "operator": "ops",
            "applied": True,
            "origin_hub_id": "edge",
        },
    )
    cross_hub = _event(
        6,
        EventKind.DEAD_LETTER_FORWARDING,
        {"target": "TEAM/agent", "origin_hub_id": "edge", "owner_hub_id": "core"},
    )

    rows = [
        universal_receipt_to_json(receipt)
        for receipt in universal_receipts_from_events(
            (
                sandbox,
                operator,
                cross_hub,
            )
        )
    ]

    assert rows[0]["kind"] == "sandbox-run"
    assert rows[0]["subject"] == "lint-tool"
    assert rows[0]["status"] == "ok"
    assert rows[1]["kind"] == "operator-relay"
    assert rows[1]["actor"] == "ops"
    assert rows[1]["status"] == "applied"
    assert rows[2]["kind"] == "cross-hub"
    assert rows[2]["status"] == "recorded"


def test_multihub_partition_and_heal_project_as_federation_receipts() -> None:
    partition = _event(
        30,
        EventKind.MULTIHUB_PARTITION,
        {
            "namespace": "OWNED",
            "local_hub_id": "syn-a",
            "owner_hub_id": "syn-a",
            "contesting_hubs": ["hub-b"],
            "outcome": "partitioned",
        },
    )
    heal = _event(
        31,
        EventKind.MULTIHUB_HEAL,
        {
            "namespace": "OWNED",
            "local_hub_id": "syn-a",
            "owner_hub_id": "syn-a",
            "previous_contesting_hubs": ["hub-b"],
            "outcome": "local",
        },
    )

    receipts = universal_receipts_from_events((partition, heal))

    assert [(receipt.kind, receipt.status) for receipt in receipts] == [
        ("federation", "partitioned"),
        ("federation", "healed"),
    ]
    assert receipts[0].subject == "OWNED"
    assert receipts[0].actor == "syn-a"
    assert universal_receipt_matches(receipts[1], "hub-b")


def test_sandbox_operator_and_selector_fallbacks_are_stable() -> None:
    sandbox = _event(
        16,
        EventKind.SANDBOX_RUN,
        {"exit": "", "reason": "fuel exhausted"},
    )
    status_operator = _event(
        17,
        EventKind.OPERATOR_RELAY,
        {"action": "approve", "namespace": "ns", "status": "queued", "agent": "relay"},
    )
    pending_operator = _event(
        18,
        EventKind.OPERATOR_RELAY,
        {"action": "relay", "namespace": "ns", "pending": True},
    )
    refused_operator = _event(
        19,
        EventKind.OPERATOR_RELAY,
        {"action": "relay", "namespace": "ns"},
    )

    receipts = universal_receipts_from_events(
        (sandbox, status_operator, pending_operator, refused_operator)
    )

    assert receipts[0].subject == ""
    assert receipts[0].status == "unknown"
    assert receipts[0].summary == "sandbox tool exited unknown: fuel exhausted"
    assert [receipt.status for receipt in receipts[1:]] == ["queued", "pending", "refused"]
    assert receipts[1].actor == "relay"
    assert receipts[2].actor == ""
    assert universal_receipt_matches(receipts[1], "")
    assert universal_receipt_matches(receipts[1], "all")
    assert not universal_receipt_matches(receipts[1], "missing")


def test_progress_notes_project_release_approval_policy_a2a_and_postmortem() -> None:
    release = _event(
        7,
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "REL",
            "author": "owner",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest; epistemic_status=supported",
        },
    )
    approval = _event(
        8,
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "gate",
            "author": "reviewer",
            "kind": "approval",
            "text": format_approval_note(subject="gate", state="approved", reason="checked"),
        },
    )
    policy = _event(
        9,
        EventKind.LEDGER_PROGRESS,
        {"author": "policy", "kind": "policy", "subject": "REL", "status": "pass", "text": "ok"},
    )
    a2a = _event(
        10,
        EventKind.LEDGER_PROGRESS,
        {
            "author": "bridge",
            "kind": "a2a_validation",
            "subject": "bridge",
            "state": "pass",
            "text": "matrix pass",
        },
    )
    postmortem = _event(
        11,
        EventKind.LEDGER_PROGRESS,
        {
            "author": "ops",
            "kind": "postmortem",
            "task_id": "REL",
            "verdict": "closed",
            "text": "incident closed",
        },
    )

    receipts = universal_receipts_from_events((release, approval, policy, a2a, postmortem))

    assert [(receipt.kind, receipt.status) for receipt in receipts] == [
        ("claim", "supported"),
        ("approval", "approved"),
        ("policy", "pass"),
        ("a2a-validation", "pass"),
        ("postmortem", "closed"),
    ]
    assert receipts[0].payload["epistemic_status"] == "supported"
    assert universal_receipt_matches(receipts[3], "a2a-validation")


def test_progress_notes_project_optional_and_malformed_fields() -> None:
    release = _event(
        20,
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "REL",
            "author": "owner",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest; malformed; epistemic_status=supported",
        },
    )
    approval = _event(
        21,
        EventKind.LEDGER_PROGRESS,
        {
            "author": "reviewer",
            "kind": "approval",
            "text": format_approval_note(subject="gate", state="requested"),
        },
    )
    verification = _event(
        22,
        EventKind.LEDGER_PROGRESS,
        {"kind": "verification", "subject": "REL", "author": None, "text": "verified"},
    )
    plain_note = _event(
        23,
        EventKind.LEDGER_PROGRESS,
        {"kind": "note", "text": "ordinary progress"},
    )

    receipts = universal_receipts_from_events((release, approval, verification, plain_note))

    assert [(receipt.kind, receipt.status) for receipt in receipts] == [
        ("claim", "supported"),
        ("approval", "requested"),
        ("verification", "recorded"),
    ]
    assert "malformed" not in receipts[0].payload
    assert receipts[1].summary == "gate requested"
    assert receipts[2].actor == ""


def test_formatting_omits_empty_optional_fields() -> None:
    receipt = UniversalReceipt(
        seq=24,
        ts=24.0,
        receipt_id="empty:24",
        kind="empty",
        subject="",
        actor="",
        status="recorded",
        summary="",
        source_event_kind="test",
        payload={"text": "value", "items": ["alpha", 3]},
    )

    assert format_universal_receipt(receipt) == "seq=24 kind=empty status=recorded"
    assert universal_receipt_matches(receipt, "value")
    assert universal_receipt_matches(receipt, "3")
    assert not universal_receipt_matches(receipt, "absent")


def test_non_receipt_events_and_malformed_approval_notes_are_ignored() -> None:
    chat = _event(12, EventKind.CHAT, {"sender": "alice", "payload": "hello"})
    malformed = _event(
        13,
        EventKind.LEDGER_PROGRESS,
        {"kind": "approval", "author": "alice", "text": "approval state=approved"},
    )

    assert universal_receipt_from_event(chat) is None
    assert universal_receipt_from_event(malformed) is None
    assert universal_receipts_from_events((chat, malformed)) == ()
