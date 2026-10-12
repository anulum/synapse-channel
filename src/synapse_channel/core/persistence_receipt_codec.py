# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""Finite JSON and receipt transition preparation, before any transaction."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from synapse_channel.core.persistence import (
        DeliveryReceiptState,
        DeliveryReceiptTransition,
    )
logger = logging.getLogger("synapse_channel.core.persistence")


def encode_object(value: Mapping[str, Any]) -> str:
    """Encode one bounded JSON object deterministically and reject non-finite data."""
    return json.dumps(
        dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def prepare_transition(
    transition: DeliveryReceiptTransition,
) -> tuple[str, int, str, dict[str, Any], dict[str, Any], DeliveryReceiptState]:
    """Validate and normalize one transition before opening the transaction."""
    receipt = dict(transition.payload)
    message_seq = receipt.get("message_seq")
    if isinstance(message_seq, bool) or not isinstance(message_seq, int) or message_seq < 1:
        raise ValueError("delivery receipt transition requires a positive message sequence")
    if not isinstance(receipt.get("delivered"), bool):
        raise ValueError("delivery receipt transition requires a boolean delivered verdict")
    expected_kind = {
        "immediate_delivered": "delivery_receipt_immediate",
        "pending": "delivery_receipt_immediate",
        "deferred": "delivery_receipt_deferred",
        "expired": "delivery_receipt_expired",
    }[transition.state]
    if transition.kind != expected_kind:
        raise ValueError("delivery receipt transition kind does not match its state")
    notification_id = f"delivery-receipt:{message_seq}:{transition.state}"
    frame = dict(transition.notification)
    frame["receipt_notification_id"] = notification_id
    return (notification_id, message_seq, transition.kind, receipt, frame, transition.state)


def _legacy_identity(payload: dict[str, Any]) -> tuple[int, int, str, str] | None:
    """Validate the original legacy cursor/id and participant projection."""
    message_seq, message_id = payload.get("message_seq"), payload.get("message_id")
    sender, target = str(payload.get("sender") or ""), str(payload.get("target") or "")
    if (
        isinstance(message_seq, bool)
        or not isinstance(message_seq, int)
        or message_seq < 1
        or isinstance(message_id, bool)
        or not isinstance(message_id, int)
        or not sender
        or not target
    ):
        return None
    return message_seq, message_id, sender, target


def _legacy_state(kind: str, payload: dict[str, Any]) -> tuple[str, int | None, int]:
    """Derive the original four-kind receipt state without changing legacy coercion."""
    if kind == "delivery_receipt_requested":
        return "requested", None, 0
    if kind == "delivery_receipt_immediate":
        delivered = int(bool(payload.get("delivered")))
        return ("immediate_delivered" if delivered else "pending"), delivered, 0
    if kind == "delivery_receipt_deferred":
        return "deferred", 1, 1
    return "expired", 0, 1


def legacy_receipt_row(
    event_seq: int, kind: str, encoded: object
) -> tuple[int, str, str, int, str, str, int | None, int, str, int] | None:
    """Decode one legacy receipt into the additive aggregate's exact column order.

    Parameters
    ----------
    event_seq : int
        Exact source event sequence.
    kind : str
        One of the four legacy receipt event kinds selected by the owner.
    encoded : object
        Original DB-API payload; legacy string conversion is retained.

    Returns
    -------
    tuple or None
        Ten aggregate columns, or None for a malformed legacy fact.
    """
    try:
        payload = json.loads(str(encoded))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    identity = _legacy_identity(payload)
    if identity is None:
        return None
    message_seq, message_id, sender, target = identity
    state, delivered, deferred = _legacy_state(kind, payload)
    return (
        message_seq,
        sender,
        target,
        message_id,
        str(payload.get("client_msg_id") or ""),
        state,
        delivered,
        deferred,
        str(payload.get("acked_by") or ""),
        int(event_seq),
    )
