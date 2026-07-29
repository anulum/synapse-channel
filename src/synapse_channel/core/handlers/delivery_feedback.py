# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — sender-visible liveness warnings and delivery receipts
"""Build private sender feedback for one chat-delivery verdict.

The messaging handler owns routing and durability. This module owns the two
private responses that explain its result to the sender: an advisory liveness
warning for matched-but-questionable recipients and the authoritative delivery
receipt. Keeping those renderings here prevents the hot chat handler from also
becoming a receipt, audit-projection, and wake-capability module.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Literal

from synapse_channel.core.agent_liveness import waiter_sidecar_names
from synapse_channel.core.delivery_receipts import (
    deferred_receipt_payload,
    expired_receipt_payload,
    immediate_receipt_payload,
    requested_receipt_payload,
)
from synapse_channel.core.directed_delivery_liveness import (
    NO_LIVE_RECIPIENT,
    DeliveryLiveness,
)
from synapse_channel.core.journal import (
    EventKind,
    record_delivery_receipt_expired,
    record_delivery_receipt_immediate,
    record_delivery_receipt_requested,
)
from synapse_channel.core.pending_receipts import ReceiptEntry
from synapse_channel.core.persistence import DeliveryReceiptTransition
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.wake_capability import (
    WAKE_PASSIVE,
    WAKE_UNKNOWN,
    wake_capability_label,
)

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


def _recipient_wake_capability(hub: SynapseHub, recipient: str) -> str:
    """Return the best declared wake capability for one logical recipient."""
    direct = hub.wake_capability_of(recipient)
    if direct != WAKE_UNKNOWN:
        return direct
    mailbox, pane = waiter_sidecar_names(recipient)
    pane_capability = hub.wake_capability_of(pane)
    if pane_capability != WAKE_UNKNOWN:
        return pane_capability
    return hub.wake_capability_of(mailbox)


def _recipient_wake_capabilities(hub: SynapseHub, recipients: Iterable[str]) -> dict[str, str]:
    """Return normalized wake capabilities keyed by logical recipient name."""
    return {recipient: _recipient_wake_capability(hub, recipient) for recipient in recipients}


def _render_recipient_with_capability(recipient: str, capability: str) -> str:
    """Render one receipt recipient with its declared wake-capability label."""
    if capability == WAKE_UNKNOWN:
        return recipient
    return f"{recipient} ({wake_capability_label(capability)})"


def _failure_payload(target: str, decision: DeliveryLiveness) -> str:
    """Return the human delivery-failure line for a negative verdict."""
    if decision.reason == NO_LIVE_RECIPIENT:
        stale = ", ".join(decision.stale_recipients)
        return f"delivery failed: no live recipient matched {target}; stale sockets: {stale}"
    return f"delivery failed: no online recipient matched {target}"


def _delivery_receipt_frame(
    hub: SynapseHub,
    *,
    sender: str,
    target: str,
    msg_id: int,
    decision: DeliveryLiveness,
    message_seq: int | None = None,
    dead_lettered: bool = False,
    client_msg_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the exact wire frame and durable payload for an immediate verdict."""
    capabilities = _recipient_wake_capabilities(hub, decision.matched_recipients)
    if decision.delivered:
        rendered = (
            _render_recipient_with_capability(recipient, capabilities[recipient])
            for recipient in decision.live_recipients
        )
        text = f"delivered to {', '.join(rendered)}"
    else:
        text = _failure_payload(target, decision)
    recorded_dead_letter = bool(dead_lettered and not decision.delivered)
    audit = immediate_receipt_payload(
        sender=sender,
        target=target,
        message_id=msg_id,
        message_seq=message_seq or 0,
        delivered=decision.delivered,
        recipients=decision.live_recipients,
        matched_recipients=decision.matched_recipients,
        stale_recipients=decision.stale_recipients,
        reason=decision.reason,
        dead_lettered=recorded_dead_letter,
        recipient_wake_capabilities=capabilities,
        client_msg_id=client_msg_id,
    )
    correlation = {"client_msg_id": client_msg_id} if client_msg_id else {}
    frame = hub._system(
        text,
        msg_type=MessageType.DELIVERY_RECEIPT,
        target=sender,
        message_target=target,
        message_id=msg_id,
        **({"message_seq": message_seq} if message_seq is not None else {}),
        delivered=decision.delivered,
        deferred=False,
        recipients=list(decision.live_recipients),
        matched_recipients=list(decision.matched_recipients),
        stale_recipients=list(decision.stale_recipients),
        reason=decision.reason,
        dead_lettered=recorded_dead_letter,
        recipient_wake_capabilities=capabilities,
        **correlation,
    )
    return frame, audit


async def warn_stale_recipients(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    target: str,
    msg_id: int,
    decision: DeliveryLiveness,
) -> None:
    """Privately explain stale or passive matches to the directed sender.

    Parameters
    ----------
    hub : SynapseHub
        Hub transport and wake-capability view.
    websocket : object
        Sender socket receiving the private warning.
    sender, target : str
        Sender identity and original message target.
    msg_id : int
        Hub-local chat identifier tied to the warning.
    decision : DeliveryLiveness
        Consume-liveness partition computed before routing.
    """
    capabilities = _recipient_wake_capabilities(hub, decision.matched_recipients)
    passive = tuple(
        recipient for recipient, capability in capabilities.items() if capability == WAKE_PASSIVE
    )
    if not decision.stale_recipients and not passive:
        return
    clauses: list[str] = []
    if decision.stale_recipients:
        clauses.append(
            f"{', '.join(decision.stale_recipients)} present but not proven live — no armed "
            "waiter and no recent reaction"
        )
    if passive:
        clauses.append(
            f"{', '.join(passive)} reached only a passive receiver — socket delivery does "
            "not prove an agent pane was woken"
        )
    if not decision.delivered:
        clauses.append("delivery classified no_live_recipient and dead-lettered")
    await hub._send_json(
        websocket,
        hub._system(
            "; ".join(clauses) + "; a directed message may sit unread",
            msg_type=MessageType.RECIPIENT_LIVENESS_WARNING,
            target=sender,
            message_target=target,
            message_id=msg_id,
            delivered=decision.delivered,
            dead_lettered=not decision.delivered,
            reason=decision.reason,
            matched_recipients=list(decision.matched_recipients),
            stale_recipients=list(decision.stale_recipients),
            passive_recipients=list(passive),
            recipient_wake_capabilities=capabilities,
        ),
    )


async def send_delivery_receipt(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    target: str,
    msg_id: int,
    decision: DeliveryLiveness,
    message_seq: int | None = None,
    dead_lettered: bool = False,
    client_msg_id: str = "",
) -> None:
    """Send and, when possible, journal one authoritative delivery receipt.

    Parameters
    ----------
    hub : SynapseHub
        Hub transport and optional durable journal.
    websocket : object
        Sender socket receiving the private receipt.
    sender, target : str
        Sender identity and original message target.
    msg_id : int
        Hub-local chat identifier tied to the receipt.
    decision : DeliveryLiveness
        Consume-liveness partition computed before routing.
    message_seq : int or None, optional
        Durable chat sequence, when the hub has a journal.
    dead_lettered : bool, optional
        Whether the directed message was recorded in the blackhole ledger.
    client_msg_id : str, optional
        Sender-chosen identity echoed so retries can be correlated downstream.
    """
    frame, audit = _delivery_receipt_frame(
        hub,
        sender=sender,
        target=target,
        msg_id=msg_id,
        message_seq=message_seq,
        decision=decision,
        dead_lettered=dead_lettered,
        client_msg_id=client_msg_id,
    )
    if hub.journal is not None and message_seq is not None:
        record_delivery_receipt_immediate(hub.journal, audit)
    await hub._send_json(websocket, frame)


def commit_delivery_receipt_request(
    hub: SynapseHub,
    *,
    chat: dict[str, Any],
    sender: str,
    target: str,
    msg_id: int,
    client_msg_id: str = "",
) -> int:
    """Atomically journal a directed chat and its receipt-requested aggregate."""
    if hub.journal is None:
        raise RuntimeError("durable receipt request requires a journal")
    return hub.journal.commit_delivery_receipt_request(
        chat=chat,
        requested=requested_receipt_payload(
            sender=sender,
            target=target,
            message_id=msg_id,
            message_seq=None,
            client_msg_id=client_msg_id,
        ),
    )


async def commit_delivery_receipt_verdict(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    target: str,
    msg_id: int,
    message_seq: int,
    decision: DeliveryLiveness,
    directed: bool,
    client_msg_id: str = "",
) -> None:
    """Commit an immediate verdict with its outbox, then attempt sender delivery."""
    if hub.journal is None:
        raise RuntimeError("durable receipt verdict requires a journal")
    frame, audit = _delivery_receipt_frame(
        hub,
        sender=sender,
        target=target,
        msg_id=msg_id,
        message_seq=message_seq,
        decision=decision,
        dead_lettered=directed and not decision.delivered,
        client_msg_id=client_msg_id,
    )
    state: Literal["immediate_delivered", "pending"] = (
        "immediate_delivered" if decision.delivered else "pending"
    )
    evicted = (
        hub.pending_receipts.eviction_for(message_seq)
        if not decision.delivered and directed
        else None
    )
    companion = None
    if evicted is not None:
        evicted_seq, evicted_entry = evicted
        expired_audit, expired_frame = _expired_receipt_transition(hub, evicted_seq, evicted_entry)
        companion = DeliveryReceiptTransition(
            EventKind.DELIVERY_RECEIPT_EXPIRED,
            expired_audit,
            expired_frame,
            "expired",
        )
    hub.journal.commit_delivery_receipt_transition(
        kind=EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        payload=audit,
        notification=frame,
        state=state,
        companion=companion,
    )
    if not decision.delivered and directed:
        remembered_eviction = hub.pending_receipts.remember(
            message_seq,
            sender=sender,
            target=target,
            message_id=msg_id,
            client_msg_id=client_msg_id,
        )
        if remembered_eviction != evicted:
            raise RuntimeError("pending receipt eviction changed during its durable commit")
    await deliver_pending_receipt_notifications(hub, sender=sender, websocket=websocket)


def _expired_receipt_transition(
    hub: SynapseHub, message_seq: int, entry: ReceiptEntry
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a bounded-window expiry event and deduplicable sender notification."""
    audit = expired_receipt_payload(
        entry=entry, message_seq=message_seq, reason="pending_window_evicted"
    )
    frame = hub._system(
        "delivery receipt expired before acknowledgement",
        msg_type=MessageType.DELIVERY_RECEIPT,
        target=entry.sender,
        message_target=entry.target,
        message_id=entry.message_id,
        message_seq=message_seq,
        delivered=False,
        deferred=True,
        expired=True,
        reason="pending_window_evicted",
        recipients=[],
        **({"client_msg_id": entry.client_msg_id} if entry.client_msg_id else {}),
    )
    return audit, frame


async def settle_delivery_receipt(
    hub: SynapseHub,
    *,
    message_seq: int,
    entry: ReceiptEntry,
    recipient: str,
) -> None:
    """Atomically settle a pending receipt and enqueue its deferred notification."""
    if hub.journal is None:
        hub.pending_receipts.claim(message_seq)
        return
    audit = deferred_receipt_payload(entry=entry, message_seq=message_seq, recipient=recipient)
    frame = hub._system(
        f"delivered to {recipient} on reconnect",
        msg_type=MessageType.DELIVERY_RECEIPT,
        target=entry.sender,
        message_target=entry.target,
        message_id=entry.message_id,
        message_seq=message_seq,
        delivered=True,
        deferred=True,
        recipients=[recipient],
        **({"client_msg_id": entry.client_msg_id} if entry.client_msg_id else {}),
    )
    hub.journal.commit_delivery_receipt_transition(
        kind=EventKind.DELIVERY_RECEIPT_DEFERRED,
        payload=audit,
        notification=frame,
        state="deferred",
    )
    hub.pending_receipts.claim(message_seq)
    await deliver_pending_receipt_notifications(hub, sender=entry.sender)


async def deliver_pending_receipt_notifications(
    hub: SynapseHub,
    *,
    sender: str,
    websocket: Any | None = None,
) -> None:
    """Attempt stable outbox frames for ``sender`` and retain failures for retry."""
    if hub.journal is None:
        return
    for pending in hub.journal.pending_delivery_notifications(sender=sender):
        delivered = False
        if websocket is not None:
            try:
                await hub._send_json(websocket, pending.frame)
                delivered = True
            except Exception:
                delivered = False
        else:
            delivered = await hub._send_to_agent(sender, pending.frame)
        hub.journal.mark_delivery_notification_attempt(pending.notification_id, delivered=delivered)


async def send_and_track_delivery_receipt(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    target: str,
    msg_id: int,
    message_seq: int | None,
    decision: DeliveryLiveness,
    directed: bool,
    client_msg_id: str = "",
) -> None:
    """Audit, send, and retain the deferred path for one requested receipt.

    Parameters
    ----------
    hub : SynapseHub
        Hub receipt journal and bounded pending-receipt store.
    websocket : object
        Sender socket receiving the immediate verdict.
    sender, target : str
        Sender identity and original message target.
    msg_id : int
        Hub-local chat identifier tied to the receipt.
    message_seq : int or None
        Durable chat sequence, when the hub has a journal.
    decision : DeliveryLiveness
        Consume-liveness partition computed before routing.
    directed : bool
        Whether this target is eligible for dead-letter and deferred-replay handling.
    client_msg_id : str, optional
        Sender-chosen identity echoed by immediate and deferred receipts.

    Notes
    -----
    A caller may invoke this before stale-socket fan-out. That ordering guarantees
    the pending entry exists before a mailbox client can ACK the live frame; offline
    and positive paths may invoke it after fan-out to preserve their historical
    sender-visible ordering.
    """
    if hub.journal is not None and message_seq is not None:
        record_delivery_receipt_requested(
            hub.journal,
            requested_receipt_payload(
                sender=sender,
                target=target,
                message_id=msg_id,
                message_seq=message_seq,
                client_msg_id=client_msg_id,
            ),
        )
    await send_delivery_receipt(
        hub,
        websocket,
        sender=sender,
        target=target,
        msg_id=msg_id,
        message_seq=message_seq,
        decision=decision,
        dead_lettered=directed and not decision.delivered,
        client_msg_id=client_msg_id,
    )
    if decision.delivered or not directed or message_seq is None:
        return
    evicted = hub.pending_receipts.remember(
        message_seq,
        sender=sender,
        target=target,
        message_id=msg_id,
        client_msg_id=client_msg_id,
    )
    if evicted is not None and hub.journal is not None:
        evicted_seq, evicted_entry = evicted
        record_delivery_receipt_expired(
            hub.journal,
            expired_receipt_payload(
                entry=evicted_entry,
                message_seq=evicted_seq,
                reason="pending_window_evicted",
            ),
        )
