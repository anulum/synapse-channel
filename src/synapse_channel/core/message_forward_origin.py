# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — origin-side routing of chats addressed to seats on peer hubs
"""Origin side of cross-hub chat: accept, retain, forward, retry and report.

An agent writes a chat to ``PROJECT/seat@HUB_ID``. The chat router
(:func:`~synapse_channel.core.handlers.messaging.route_chat`) hands it here after the sender's
own quota check. This module then:

1. refuses a target that is not exactly one directed seat on a configured message peer;
2. retains and journals the chat locally, exactly as a local chat, so the feed shows what left
   the hub (the hub-qualified target is never counted as a local mailbox);
3. writes it to the durable outbox
   (:class:`~synapse_channel.core.message_forward_ledger.MessageForwardLedger`) before the
   first attempt, then attempts the forward once immediately;
4. answers a requested receipt with what is known now — delivered or queued on the peer,
   refused by the peer, or pending while the peer is unreachable.

Pending forwards are retried by :func:`message_forward_retry_loop` with exponential backoff until
the peer answers or the forward expires. A sender that asked for a receipt is told the settled
outcome, immediately when online or on its next registration otherwise
(:func:`deliver_pending_forward_receipts`). Delivery is at least once: a forward whose answer
was lost is resent with the same ``forward_id`` and the peer answers ``duplicate``.

A receipt reports transport facts only. ``delivered`` means a consume-live recipient on the
peer received the chat, as a local receipt does; it never means the recipient acted on it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.hub_address import (
    HubQualifiedAddress,
    is_single_seat,
    parse_hub_qualified,
)
from synapse_channel.core.journal import record_chat
from synapse_channel.core.message_forward_attempts import own_forward_work
from synapse_channel.core.message_forward_ledger import OutboxEntry, RemoteDelivery
from synapse_channel.core.message_forward_transport import (
    MessageForwardRejectedError,
    MessageForwardTransportError,
)
from synapse_channel.core.message_forward_wire import (
    ForwardKind,
    MessageForwardRequest,
    MessageForwardWireError,
    decode_message_forward_request,
    encode_message_forward_request,
    encode_message_forward_result,
)
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.message_forward_ledger import MessageForwardLedger
    from synapse_channel.core.message_forward_transport import MessageForwarder, MessageForwardPeer

    class ForwardOriginContext(HandlerContext, Protocol):
        """Capabilities consumed by message forward origin handlers and their callees."""

        @property
        def chat_history(self) -> list[dict[str, Any]]:
            """Return the chat history used by this handler family."""
            ...

        @property
        def max_history(self) -> int:
            """Return the max history used by this handler family."""
            ...

        @property
        def message_forward_ledger(self) -> MessageForwardLedger:
            """Return the message forward ledger used by this handler family."""
            ...

        @property
        def message_forward_ttl(self) -> float:
            """Return the message forward ttl used by this handler family."""
            ...

        @property
        def message_forwarder(self) -> MessageForwarder:
            """Return the message forwarder used by this handler family."""
            ...

        @property
        def message_peers(self) -> dict[str, MessageForwardPeer] | None:
            """Return the message peers used by this handler family."""
            ...

        @property
        def private_directed_messages(self) -> bool:
            """Return the private directed messages used by this handler family."""
            ...


logger = logging.getLogger("synapse.message_forward")

DEFAULT_FORWARD_TTL_SECONDS = 86_400.0
"""How long an unanswered forwarded chat is retried before it expires (24 hours)."""

MAX_RETRY_DELAY_SECONDS = 300.0
"""Longest wait between two attempts of one pending forward."""

RETRY_SWEEP_INTERVAL_SECONDS = 1.0
"""How often the retry loop looks for due forwards."""

FORWARD_PENDING = "forward_pending"
FORWARD_REFUSED = "forward_refused"
FORWARD_EXPIRED = "forward_expired"


def retry_delay(attempts: int) -> float:
    """Return the wait before the next attempt after ``attempts`` failed ones.

    Parameters
    ----------
    attempts : int
        Failed attempts so far, including the one that just failed.

    Returns
    -------
    float
        ``2 ** (attempts - 1)`` seconds, at least 1 and at most
        :data:`MAX_RETRY_DELAY_SECONDS`.
    """
    exponent = max(0, min(int(attempts) - 1, 16))
    return min(MAX_RETRY_DELAY_SECONDS, float(2**exponent))


def chat_target_refusal(target: str) -> str:
    """Return why ``target`` cannot be forwarded, or an empty string when it can.

    Parameters
    ----------
    target : str
        The chat's target as the sender wrote it; it contains ``@``.

    Returns
    -------
    str
        A refusal for a comma list, an audience or glob, or a malformed address; empty for
        exactly one directed hub-qualified seat.
    """
    if "," in target:
        return "A cross-hub target must be exactly one seat: PROJECT/seat@HUB_ID."
    address = parse_hub_qualified(target.strip())
    if address is None:
        return f"'{target}' is not a valid hub-qualified seat (PROJECT/seat@HUB_ID)."
    if not is_single_seat(address.seat):
        return "A cross-hub target must name one seat, not an audience or a glob."
    return ""


async def forward_chat(
    hub: ForwardOriginContext, sender: str, data: dict[str, Any], websocket: Any
) -> bool:
    """Accept one agent chat addressed to a seat on a peer hub and forward it.

    Parameters
    ----------
    hub : ForwardOriginContext
        The origin hub. ``data`` has already passed the sender's quota and been stamped with
        ``timestamp``, ``msg_id`` and ``hub_id``.
    sender : str
        The authenticated local sender.
    data : dict[str, Any]
        The chat frame.
    websocket : Any
        The sender's connection; refusals and the receipt go there.

    Returns
    -------
    bool
        ``True`` when the chat was accepted into the outbox, ``False`` when refused.
    """
    target = str(data.get("target") or "").strip()
    refusal = chat_target_refusal(target)
    address = parse_hub_qualified(target)
    peers = hub.message_peers or {}
    if not refusal and address is not None and address.hub_id not in peers:
        refusal = f"Hub '{address.hub_id}' is not a configured message peer of this hub."
    if refusal or address is None:
        await hub.send_json(
            websocket, hub.system(refusal, msg_type=MessageType.ERROR, target=sender)
        )
        return False
    data["target"] = str(address)
    client_msg_id = str(data.get("client_msg_id") or "")
    body: dict[str, Any] = {
        "payload": str(data.get("payload") or ""),
        "origin_msg_id": int(data["msg_id"]),
        "origin_timestamp": float(data["timestamp"]),
    }
    if client_msg_id:
        body["client_msg_id"] = client_msg_id
    forward_id = uuid.uuid4().hex
    try:
        fields = encode_message_forward_request(
            MessageForwardRequest(
                forward_id=forward_id,
                kind="chat",
                sender_seat=sender,
                target_seat=address.seat,
                body=body,
            )
        )
    except MessageForwardWireError as exc:
        await hub.send_json(
            websocket,
            hub.system(
                f"Chat cannot be forwarded: {exc}", msg_type=MessageType.ERROR, target=sender
            ),
        )
        return False
    hub.chat_history.append(data.copy())
    if len(hub.chat_history) > hub.max_history:
        del hub.chat_history[0]
    if hub.journal is not None:
        data["seq"] = record_chat(hub.journal, data)
    data["forward_id"] = forward_id
    now = time.time()
    entry = hub.message_forward_ledger.enqueue(
        forward_id=forward_id,
        peer_hub=address.hub_id,
        sender=sender,
        target=str(address),
        request=fields,
        now=now,
        expires_at=now + hub.message_forward_ttl,
        notify_sender=bool(data.get("receipt_requested")),
    )
    hub.counters.chat_directed += 1
    if not hub.private_directed_messages:
        await hub.broadcast(data)
    settled = await attempt_forward(hub, entry)
    if entry.notify_sender:
        await _initial_forward_receipt(hub, settled, data, websocket)
    return True


async def _initial_forward_receipt(
    hub: ForwardOriginContext, entry: OutboxEntry, chat: dict[str, Any], websocket: Any
) -> None:
    """Report initial queueing privately or project the shared terminal receipt once."""
    if entry.state == "pending":
        await hub.send_json(websocket, forward_receipt_frame(hub, entry, chat))
    else:
        await _notify_sender(hub, entry, chat=chat)


async def attempt_forward(hub: ForwardOriginContext, entry: OutboxEntry) -> OutboxEntry:
    """Make one attempt to forward a pending outbox entry and record the outcome.

    Parameters
    ----------
    hub : ForwardOriginContext
        The origin hub.
    entry : OutboxEntry
        A pending entry.

    Returns
    -------
    OutboxEntry
        The entry after this attempt: settled on an answer or a peer rejection, still pending
        with a rescheduled attempt on a transport failure.
    """
    ledger = hub.message_forward_ledger
    with own_forward_work(ledger, entry.forward_id, "attempt") as owned:
        current = ledger.outbox_entry(entry.forward_id) or entry
        if not owned or current.state != "pending":
            return current
        return await _attempt_owned_forward(hub, current)


async def _attempt_owned_forward(hub: ForwardOriginContext, entry: OutboxEntry) -> OutboxEntry:
    """Perform the peer exchange while holding this forward's transient ownership."""
    ledger = hub.message_forward_ledger
    peer = (hub.message_peers or {}).get(entry.peer_hub)
    if peer is None:
        settled = ledger.settle(
            entry.forward_id,
            "refused",
            {
                "reason_code": "peer_not_configured",
                "detail": f"{entry.peer_hub} is no longer a peer",
            },
        )
        return settled or entry
    try:
        request = decode_message_forward_request(entry.request)
        result = await hub.message_forwarder(request, peer=peer, local_id=hub.hub_id)
    except MessageForwardRejectedError:
        settled = ledger.settle(
            entry.forward_id,
            "refused",
            {"reason_code": "peer_rejected", "detail": "The peer refused this forward."},
        )
        return settled or entry
    except (MessageForwardTransportError, MessageForwardWireError) as exc:
        attempts = entry.attempts + 1
        logger.warning(
            "Forward %s to %s failed (attempt %d): %s",
            entry.forward_id,
            entry.peer_hub,
            attempts,
            type(exc).__name__,
        )
        updated = ledger.record_failed_attempt(
            entry.forward_id,
            next_attempt_at=time.time() + retry_delay(attempts),
            error="The peer connection failed or returned an invalid answer.",
        )
        return updated or entry
    settled = ledger.settle(
        entry.forward_id, result.disposition, encode_message_forward_result(result)
    )
    return settled or entry


async def run_forward_retries(hub: ForwardOriginContext, *, now: float) -> int:
    """Expire overdue forwards, retry due ones, and tell waiting senders what settled.

    Parameters
    ----------
    hub : ForwardOriginContext
        The origin hub.
    now : float
        Current wall-clock time.

    Returns
    -------
    int
        Entries settled by this sweep (expired or answered).
    """
    ledger = hub.message_forward_ledger
    settled: list[OutboxEntry] = list(ledger.expire_due(now))
    for entry in ledger.due(now):
        current = ledger.outbox_entry(entry.forward_id)
        if current is None or current.state != "pending":
            continue
        outcome = await attempt_forward(hub, entry)
        if outcome.state not in ("pending", "expired"):
            settled.append(outcome)
    for entry in settled:
        if entry.notify_sender:
            await _notify_sender(hub, entry)
    return len(settled)


async def message_forward_retry_loop(
    hub: ForwardOriginContext, *, interval: float = RETRY_SWEEP_INTERVAL_SECONDS
) -> None:
    """Run :func:`run_forward_retries` until cancelled.

    Parameters
    ----------
    hub : ForwardOriginContext
        The origin hub.
    interval : float, optional
        Seconds between sweeps.
    """
    while True:
        await asyncio.sleep(interval)
        await run_forward_retries(hub, now=time.time())


async def deliver_pending_forward_receipts(hub: ForwardOriginContext, *, sender: str) -> None:
    """Send ``sender`` every settled forward outcome it asked for and has not received.

    Called when a seat registers, so a sender that was offline when its forward settled still
    learns the outcome. A send that fails leaves the notification pending for the next
    registration.

    Parameters
    ----------
    hub : ForwardOriginContext
        The origin hub.
    sender : str
        The seat that just registered.
    """
    for entry in hub.message_forward_ledger.pending_sender_notifications(sender):
        await _notify_sender(hub, entry)


async def _notify_sender(
    hub: ForwardOriginContext, entry: OutboxEntry, *, chat: dict[str, Any] | None = None
) -> None:
    """Send a settled outcome to its sender when online; otherwise it waits for registration."""
    ledger = hub.message_forward_ledger
    with own_forward_work(ledger, entry.forward_id, "notification") as owned:
        if not owned or not ledger.sender_notification_pending(entry.forward_id):
            return
        if await hub.send_to_agent(entry.sender, forward_receipt_frame(hub, entry, chat)):
            ledger.mark_sender_notified(entry.forward_id, now=time.time())


def forward_receipt_frame(
    hub: ForwardOriginContext, entry: OutboxEntry, chat: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the ``delivery_receipt`` a sender receives for a forwarded chat.

    Parameters
    ----------
    hub : ForwardOriginContext
        The origin hub.
    entry : OutboxEntry
        The forward's current outbox state.
    chat : dict[str, Any] or None, optional
        The original chat frame when available; supplies ``message_seq``.

    Returns
    -------
    dict[str, Any]
        A ``delivery_receipt`` frame. ``delivered`` is true only when the peer reported a
        consume-live recipient; ``deferred`` is true while the forward is pending and for any
        outcome reported after the first receipt.
    """
    request = entry.request
    body = _object(request.get("body"))
    address = parse_hub_qualified(entry.target) or HubQualifiedAddress(entry.target, entry.peer_hub)
    result = _object(entry.result.get("result"))
    delivered = False
    recipients: list[str] = []
    matched: list[str] = []
    stale: list[str] = []
    dead_lettered = False
    if entry.state in ("accepted", "duplicate"):
        delivered = bool(result.get("delivered"))
        recipients = _qualified(result.get("recipients"), address.hub_id)
        matched = _qualified(result.get("matched_recipients"), address.hub_id)
        stale = _qualified(result.get("stale_recipients"), address.hub_id)
        dead_lettered = bool(result.get("dead_lettered"))
        reason = str(result.get("reason") or "")
        if delivered:
            text = f"delivered to {', '.join(recipients)} (forwarded to {address.hub_id})"
        else:
            text = (
                f"forwarded to {address.hub_id}; no live recipient matched {address} there — "
                "it waits in that hub's mailbox"
            )
    elif entry.state == "pending":
        reason = FORWARD_PENDING
        error = str(entry.result.get("last_error") or "not attempted yet")
        text = f"forward to {address.hub_id} pending ({error}); retrying"
    elif entry.state == "refused":
        reason = FORWARD_REFUSED
        code = str(entry.result.get("reason_code") or "refused")
        detail = str(entry.result.get("detail") or "")
        text = f"forward to {address.hub_id} refused ({code}){': ' + detail if detail else ''}"
    else:
        reason = FORWARD_EXPIRED
        text = f"forward to {address.hub_id} expired: the peer did not answer in time"
    fields: dict[str, Any] = {
        "message_target": entry.target,
        "message_id": int(body.get("origin_msg_id") or 0),
        "delivered": delivered,
        "deferred": chat is None or entry.state == "pending",
        "recipients": recipients,
        "matched_recipients": matched,
        "stale_recipients": stale,
        "reason": reason,
        "dead_lettered": dead_lettered,
        "recipient_wake_capabilities": {},
        "forward_id": entry.forward_id,
        "forwarded_to": entry.peer_hub,
        "forward_state": entry.state,
    }
    if chat is not None and "seq" in chat:
        fields["message_seq"] = int(chat["seq"])
    client_msg_id = body.get("client_msg_id")
    if isinstance(client_msg_id, str) and client_msg_id:
        fields["client_msg_id"] = client_msg_id
    return hub.system(text, msg_type=MessageType.DELIVERY_RECEIPT, target=entry.sender, **fields)


def _object(value: object) -> dict[str, Any]:
    """Return ``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def _qualified(names: object, hub_id: str) -> list[str]:
    """Qualify a peer's recipient names with its hub id, dropping anything malformed."""
    if not isinstance(names, list):
        return []
    return [f"{name}@{hub_id}" for name in names if isinstance(name, str) and name]


_HEX_DIGITS = frozenset("0123456789abcdef")

DELIVERY_FORWARD_FIELDS = (
    "payload",
    "protocol_version",
    "request_id",
    "idempotency_key",
    "target_incarnation",
    "mode",
    "allowed_fallbacks",
    "task_id",
    "body",
    "deadline",
)
"""Delivery-request fields forwarded to the peer; identity and credentials never are."""


async def _forward_once(
    hub: ForwardOriginContext, peer_hub: str, request: MessageForwardRequest
) -> dict[str, Any]:
    """Forward one synchronous request and return the peer's result payload.

    Raises
    ------
    DeliveryRefusal
        ``unknown_hub`` for an unconfigured peer, ``peer_unreachable`` for a transport
        failure, ``peer_rejected`` for an error frame, or the peer's own reason code for a
        refusal.
    """
    peer = (hub.message_peers or {}).get(peer_hub)
    if peer is None:
        raise DeliveryRefusal("unknown_hub", f"hub {peer_hub!r} is not a configured message peer")
    try:
        result = await hub.message_forwarder(request, peer=peer, local_id=hub.hub_id)
    except MessageForwardTransportError as exc:
        raise DeliveryRefusal("peer_unreachable", "The peer connection failed.") from exc
    except MessageForwardRejectedError as exc:
        raise DeliveryRefusal("peer_rejected", "The peer refused this request.") from exc
    if result.disposition == "refused":
        raise DeliveryRefusal(result.reason_code, result.detail or "peer refused the request")
    return dict(result.result)


def _relayed_status(payload: dict[str, Any], *, sender: str, peer_hub: str) -> dict[str, Any]:
    """Re-address a peer's ``delivery_status`` frame to the local requester."""
    status = payload.get("status")
    if not isinstance(status, dict) or status.get("type") != MessageType.DELIVERY_STATUS:
        raise DeliveryRefusal("peer_invalid_answer", "peer answered without a delivery status")
    key = status.get("operation_key")
    if not isinstance(key, str) or len(key) != 64 or any(c not in _HEX_DIGITS for c in key):
        raise DeliveryRefusal("peer_invalid_answer", "peer status has no operation key")
    relayed = dict(status)
    relayed["target"] = sender
    relayed["remote_hub"] = peer_hub
    return relayed


def _refuse_shadowing_key(
    hub: ForwardOriginContext, key: str, *, sender: str, peer_hub: str
) -> None:
    """Refuse a peer-issued operation key that would redirect another delivery's follow-ups.

    Status and cancel requests are routed by operation key, so a key the peer returns must
    not name a delivery admitted on this hub, nor a route already held for another peer or
    another local sender. Repeating the same request to the same peer returns the same key
    and passes.
    """
    local = hub.journal.delivery.get(key) if hub.journal is not None else None
    route = hub.message_forward_ledger.remote_delivery(key)
    if local is not None or (
        route is not None and (route.peer_hub, route.sender) != (peer_hub, sender)
    ):
        raise DeliveryRefusal(
            "peer_invalid_answer", "peer returned an operation key already routed on this hub"
        )


async def forward_delivery_request(
    hub: ForwardOriginContext, sender: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Forward a delivery intent for ``PROJECT/seat@HUB_ID`` and relay the peer's status.

    The peer admits the intent against its own recipient session, exactly as for a local
    requester, with the requester named ``sender@this_hub``. The returned operation key is
    remembered so later status and cancel requests route to the same peer.

    Parameters
    ----------
    hub : ForwardOriginContext
        The origin hub.
    sender : str
        The local requester (already profile-checked).
    data : dict[str, Any]
        The version-three request.

    Returns
    -------
    dict[str, Any]
        The peer's ``delivery_status`` frame, addressed to ``sender``, with ``remote_hub``.

    Raises
    ------
    DeliveryRefusal
        For an invalid target or any refusal from :func:`_forward_once`.
    """
    target = str(data.get("target") or "").strip()
    refusal = chat_target_refusal(target)
    address = parse_hub_qualified(target)
    if refusal or address is None:
        raise DeliveryRefusal("invalid_target", refusal or "invalid hub-qualified target")
    body = {field: data[field] for field in DELIVERY_FORWARD_FIELDS if field in data}
    try:
        request = MessageForwardRequest(
            forward_id=uuid.uuid4().hex,
            kind="delivery_request",
            sender_seat=sender,
            target_seat=address.seat,
            body=body,
        )
        encode_message_forward_request(request)
    except MessageForwardWireError as exc:
        raise DeliveryRefusal("invalid_shape", str(exc)) from exc
    status = _relayed_status(
        await _forward_once(hub, address.hub_id, request), sender=sender, peer_hub=address.hub_id
    )
    _refuse_shadowing_key(hub, str(status["operation_key"]), sender=sender, peer_hub=address.hub_id)
    hub.message_forward_ledger.remember_remote_delivery(
        RemoteDelivery(
            operation_key=str(status["operation_key"]),
            peer_hub=address.hub_id,
            sender=sender,
            target=str(address),
        ),
        now=time.time(),
    )
    return status


async def forward_delivery_followup(
    hub: ForwardOriginContext, sender: str, data: dict[str, Any], *, kind: ForwardKind
) -> dict[str, Any]:
    """Forward a status query or cancellation for a delivery admitted by a peer.

    Parameters
    ----------
    hub : ForwardOriginContext
        The origin hub.
    sender : str
        The local requester; only the seat that requested the delivery may follow it up.
    data : dict[str, Any]
        The status or cancel request carrying ``operation_key`` (and ``mutation_id`` for a
        cancellation).
    kind : ForwardKind
        ``delivery_status`` or ``delivery_cancel``.

    Returns
    -------
    dict[str, Any]
        The peer's ``delivery_status`` frame, addressed to ``sender``.

    Raises
    ------
    DeliveryRefusal
        ``unauthorised_requester`` for another seat, or any refusal from the peer route.
    """
    key = str(data.get("operation_key") or "")
    route = hub.message_forward_ledger.remote_delivery(key)
    if route is None or route.sender != sender:
        raise DeliveryRefusal("unauthorised_requester", "request is not visible to sender")
    body: dict[str, Any] = {"operation_key": key, "protocol_version": data.get("protocol_version")}
    if kind == "delivery_cancel":
        body["mutation_id"] = data.get("mutation_id")
    # The sender's seat already passed the wire codec when the route was created, and the
    # body is two or three bounded scalars, so this request always encodes.
    request = MessageForwardRequest(
        forward_id=uuid.uuid4().hex, kind=kind, sender_seat=sender, body=body
    )
    payload = await _forward_once(hub, route.peer_hub, request)
    return _relayed_status(payload, sender=sender, peer_hub=route.peer_hub)


async def forward_who(hub: ForwardOriginContext, sender: str, peer_hub: str) -> dict[str, Any]:
    """Return a peer hub's roster, as far as that peer lets this hub see it.

    Parameters
    ----------
    hub : ForwardOriginContext
        The origin hub.
    sender : str
        The local requester.
    peer_hub : str
        The message peer to ask.

    Returns
    -------
    dict[str, Any]
        A ``who_snapshot`` frame whose ``online_agents`` and ``delivery_sessions`` keys are
        hub-qualified (``seat@peer_hub``) and which carries ``remote_hub``, or an ``error``
        frame when the peer cannot be asked or refuses.
    """
    try:
        request = MessageForwardRequest(forward_id=uuid.uuid4().hex, kind="who", sender_seat=sender)
        payload = await _forward_once(hub, peer_hub, request)
    except (DeliveryRefusal, MessageForwardWireError) as exc:
        code = exc.code if isinstance(exc, DeliveryRefusal) else "invalid_shape"
        return hub.system(
            f"Roster of hub '{peer_hub}' unavailable ({code}): {exc}",
            msg_type=MessageType.ERROR,
            target=sender,
        )
    online = _qualified(payload.get("online_agents"), peer_hub)
    sessions_raw = _object(payload.get("delivery_sessions"))
    sessions: dict[str, Any] = {}
    for name, session in sessions_raw.items():
        if isinstance(name, str) and name and isinstance(session, dict):
            sessions[f"{name}@{peer_hub}"] = {**session, "hub_id": peer_hub}
    return hub.system(
        f"Who snapshot of hub {peer_hub}",
        msg_type=MessageType.WHO_SNAPSHOT,
        target=sender,
        online_agents=online,
        delivery_sessions=sessions,
        remote_hub=peer_hub,
    )
