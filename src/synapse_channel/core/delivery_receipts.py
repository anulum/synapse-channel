# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable delivery-receipt audit projections
"""Build and query durable audit records for directed-message delivery receipts.

Delivery receipts are wire frames first: a sender asks for one, the hub replies
privately with the live delivery result, and a later mailbox ``ack`` may revise a
dead-lettered message to delivered. This module gives those transitions a small
append-only ledger shape so receipt evidence survives sender disconnects and hub
restarts without changing the live wire protocol.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.pending_receipts import (
    DEFAULT_PENDING_RECEIPTS,
    PendingReceipts,
    ReceiptEntry,
)
from synapse_channel.core.persistence import StoredEvent

DELIVERY_RECEIPT_EVENT_KINDS = frozenset(
    {
        EventKind.DELIVERY_RECEIPT_REQUESTED,
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        EventKind.DELIVERY_RECEIPT_DEFERRED,
        EventKind.DELIVERY_RECEIPT_EXPIRED,
    }
)
"""Event kinds that make up the durable delivery-receipt ledger."""


def requested_receipt_payload(
    *,
    sender: str,
    target: str,
    message_id: int,
    message_seq: int,
) -> dict[str, Any]:
    """Return the audit payload for a sender requesting a delivery receipt."""
    return {
        "sender": sender,
        "target": target,
        "message_id": int(message_id),
        "message_seq": int(message_seq),
    }


def immediate_receipt_payload(
    *,
    sender: str,
    target: str,
    message_id: int,
    message_seq: int,
    delivered: bool,
    recipients: Iterable[str],
    matched_recipients: Iterable[str] | None = None,
    stale_recipients: Iterable[str] = (),
    reason: str = "",
    dead_lettered: bool = False,
    recipient_wake_capabilities: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the audit payload for the hub's immediate receipt verdict."""
    live = [str(recipient) for recipient in recipients]
    matched = live if matched_recipients is None else [str(item) for item in matched_recipients]
    return {
        "sender": sender,
        "target": target,
        "message_id": int(message_id),
        "message_seq": int(message_seq),
        "delivered": bool(delivered),
        "deferred": False,
        "recipients": live,
        "matched_recipients": matched,
        "stale_recipients": [str(recipient) for recipient in stale_recipients],
        "reason": str(reason),
        "dead_lettered": bool(dead_lettered),
        "recipient_wake_capabilities": dict(recipient_wake_capabilities or {}),
    }


def deferred_receipt_payload(
    *,
    entry: ReceiptEntry,
    message_seq: int,
    recipient: str,
) -> dict[str, Any]:
    """Return the audit payload for a mailbox ``ack`` settling a pending receipt."""
    return {
        "sender": entry.sender,
        "target": entry.target,
        "message_id": entry.message_id,
        "message_seq": int(message_seq),
        "delivered": True,
        "deferred": True,
        "recipients": [recipient],
        "acked_by": recipient,
    }


def expired_receipt_payload(
    *,
    entry: ReceiptEntry,
    message_seq: int,
    reason: str,
) -> dict[str, Any]:
    """Return the audit payload for a pending receipt leaving the bounded window."""
    return {
        "sender": entry.sender,
        "target": entry.target,
        "message_id": entry.message_id,
        "message_seq": int(message_seq),
        "delivered": False,
        "deferred": True,
        "expired": True,
        "reason": reason,
        "recipients": [],
    }


def restore_pending_receipts(
    events: Iterable[StoredEvent],
    *,
    max_entries: int = DEFAULT_PENDING_RECEIPTS,
) -> tuple[tuple[int, ReceiptEntry], ...]:
    """Rebuild pending deferred receipts from durable receipt events.

    The projection only reopens entries whose immediate verdict was
    ``delivered=false`` and whose later ledger has no deferred or expired event.
    This lets a hub restart after the original sender was told "not delivered":
    when the recipient later replays and acks the chat, the hub can still produce
    the deferred receipt and journal the final verdict.
    """
    pending = PendingReceipts(max_entries=max_entries)
    for event in events:
        payload = event.payload
        if event.kind == EventKind.DELIVERY_RECEIPT_IMMEDIATE and payload.get("delivered") is False:
            message_seq = _optional_int(payload.get("message_seq"))
            message_id = _optional_int(payload.get("message_id"))
            sender = _clean(payload.get("sender"))
            target = _clean(payload.get("target"))
            if message_seq is not None and message_id is not None and sender and target:
                pending.remember(
                    message_seq,
                    sender=sender,
                    target=target,
                    message_id=message_id,
                )
        elif event.kind in {
            EventKind.DELIVERY_RECEIPT_DEFERRED,
            EventKind.DELIVERY_RECEIPT_EXPIRED,
        }:
            message_seq = _optional_int(payload.get("message_seq"))
            if message_seq is not None:
                pending.claim(message_seq)
    return pending.entries()


def receipt_event_matches(event: StoredEvent, participant: str) -> bool:
    """Return whether ``event`` belongs to ``participant``'s receipt ledger view."""
    if event.kind not in DELIVERY_RECEIPT_EVENT_KINDS:
        return False
    name = participant.strip()
    if not name or name == "all":
        return True
    payload = event.payload
    recipients = payload.get("recipients")
    matched_recipients = payload.get("matched_recipients")
    return (
        str(payload.get("sender", "")) == name
        or str(payload.get("target", "")) == name
        or (isinstance(recipients, list) and name in {str(item) for item in recipients})
        or (
            isinstance(matched_recipients, list)
            and name in {str(item) for item in matched_recipients}
        )
    )


def receipt_event_to_json(event: StoredEvent) -> dict[str, Any]:
    """Return a stable JSON object for one durable receipt event."""
    payload = event.payload
    return {
        "seq": event.seq,
        "ts": event.ts,
        "kind": event.kind,
        "phase": _phase(event.kind),
        "sender": str(payload.get("sender", "")),
        "target": str(payload.get("target", "")),
        "message_id": _optional_int(payload.get("message_id")),
        "message_seq": _optional_int(payload.get("message_seq")),
        "delivered": payload.get("delivered"),
        "deferred": bool(payload.get("deferred", False)),
        "expired": bool(payload.get("expired", False)),
        "recipients": [str(item) for item in payload.get("recipients", [])]
        if isinstance(payload.get("recipients"), list)
        else [],
        "matched_recipients": [str(item) for item in payload.get("matched_recipients", [])]
        if isinstance(payload.get("matched_recipients"), list)
        else [],
        "stale_recipients": [str(item) for item in payload.get("stale_recipients", [])]
        if isinstance(payload.get("stale_recipients"), list)
        else [],
        "dead_lettered": bool(payload.get("dead_lettered", False)),
        "reason": str(payload.get("reason", "")),
        "acked_by": str(payload.get("acked_by", "")),
    }


def format_receipt_event(event: StoredEvent) -> str:
    """Render one durable receipt event as a compact operator line."""
    item = receipt_event_to_json(event)
    bits = [
        f"seq={item['seq']}",
        f"phase={item['phase']}",
        f"sender={item['sender']}",
        f"target={item['target']}",
        f"msg={item['message_id']}",
        f"chat_seq={item['message_seq']}",
    ]
    if item["delivered"] is not None:
        bits.append(f"delivered={item['delivered']}")
    recipients = item["recipients"]
    if recipients:
        bits.append(f"recipients={','.join(recipients)}")
    if item["reason"]:
        bits.append(f"reason={item['reason']}")
    return " ".join(bits)


def _phase(kind: str) -> str:
    """Return the phase suffix for one delivery-receipt event kind."""
    prefix = "delivery_receipt_"
    return kind[len(prefix) :] if kind.startswith(prefix) else kind


def _clean(value: object) -> str:
    """Return a stripped string field, or an empty string for non-strings."""
    return value.strip() if isinstance(value, str) else ""


def _optional_int(value: object) -> int | None:
    """Return ``value`` as an integer, rejecting booleans and malformed values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
