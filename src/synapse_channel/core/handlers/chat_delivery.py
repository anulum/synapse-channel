# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local chat delivery and receipt ordering
"""Deliver an accepted local chat through its original durability and fanout stages."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.agent_liveness import waiter_owner, waiter_sidecar_names
from synapse_channel.core.dead_letter_escalation import crosses_escalation_threshold
from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.directed_delivery_liveness import (
    DeliveryLiveness,
    classify_completed_delivery,
    classify_delivery_liveness,
)
from synapse_channel.core.handlers.delivery_feedback import (
    commit_delivery_receipt_request,
    commit_delivery_receipt_verdict,
    send_and_track_delivery_receipt,
    warn_stale_recipients,
)
from synapse_channel.core.journal import record_chat
from synapse_channel.core.protocol import is_recipient

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.handlers.messaging import MessagingContext

    class DeadLetterEscalation(Protocol):
        """The existing escalation callback bound to this routing hub."""

        async def __call__(self, *, target: str, count: int, sender: str) -> None:
            """Publish the already counted blackhole signal after chat fanout."""
            ...


@dataclass(frozen=True)
class ChatDeliveryRequest:
    """Accepted chat inputs retained across local delivery stages.

    Attributes
    ----------
    hub
        Hub whose state and transports are used.
    sender
        Authenticated sender or admitted forwarding identity.
    data
        Chat frame stamped by admission; journal sequence is added in place.
    websocket
        Connection to receive the sender's feedback.
    client_msg_id
        Validated retry identity, or an empty string.
    digest
        Digest computed before message identity allocation.
    remember
        Whether successful delivery enrols a retry entry.
    accepted_at
        Hub-authoritative acceptance timestamp in seconds.
    escalate
        Existing blackhole callback, preserving its forwarding behaviour.
    """

    hub: MessagingContext
    sender: str
    data: dict[str, Any]
    websocket: Any
    client_msg_id: str
    digest: str
    remember: bool
    accepted_at: float
    escalate: DeadLetterEscalation


@dataclass
class _LocalDelivery:
    """Recipient verdict and durable feedback state for one accepted chat."""

    target: str
    recipients: list[str]
    directed: bool
    decision: DeliveryLiveness
    escalation: tuple[str, int, str] | None = None
    message_seq: int | None = None
    receipt_requested: bool = False
    receipt_before_fanout: bool = False


def _resolve_audience(request: ChatDeliveryRequest) -> _LocalDelivery:
    hub, sender, data = request.hub, request.sender, request.data
    target = str(data.get("target") or "all")
    logical_target = waiter_owner(target)
    if logical_target != target:
        # ``-rx`` and ``-pane-rx`` are transport sockets, not addressable agents.
        # Owner aliases avoid classifying a live receiver as ``agent-rx-rx`` and
        # retain offline work under the identity its reconnecting receiver drains.
        target = logical_target
        data["target"] = target
    recipients = _matching_online_recipients(target, sender, hub.online_agents(), hub.roles_of)
    directed = is_directed_target(target)
    stale_recipients = hub.recipients_without_live_waiter(recipients) if directed else ()
    decision = classify_delivery_liveness(recipients, stale_recipients)
    if directed:
        hub.counters.chat_directed += 1
    else:
        hub.counters.chat_broadcast += 1
    return _LocalDelivery(target, recipients, directed, decision)


def _record_blackhole(
    request: ChatDeliveryRequest, delivery: _LocalDelivery
) -> tuple[str, int, str] | None:
    count = request.hub.dead_letters.record(
        delivery.target, sender=request.sender, ts=float(request.data["timestamp"])
    )
    if crosses_escalation_threshold(count, request.hub.dead_letter_escalation_threshold):
        return delivery.target, count, request.sender
    return None


def _retain_chat(request: ChatDeliveryRequest, delivery: _LocalDelivery) -> None:
    hub, data = request.hub, request.data
    hub.chat_history.append(data.copy())
    if len(hub.chat_history) > hub.max_history:
        del hub.chat_history[0]
    delivery.receipt_requested = bool(data.get("receipt_requested"))
    if hub.journal is not None and delivery.receipt_requested and delivery.directed:
        data["seq"] = commit_delivery_receipt_request(
            hub,
            chat=data,
            sender=request.sender,
            target=delivery.target,
            msg_id=int(data["msg_id"]),
            client_msg_id=request.client_msg_id,
        )
        hub.mailbox_pending.observe_chat(int(data["seq"]), data)
    elif hub.journal is not None:
        # Stamp the durable sequence on the outgoing frame so a client can resume
        # its missed directed backlog from that cursor. Live history precedes it.
        data["seq"] = record_chat(hub.journal, data)
        hub.mailbox_pending.observe_chat(int(data["seq"]), data)
    delivery.message_seq = int(data["seq"]) if "seq" in data else None
    delivery.receipt_before_fanout = (
        delivery.receipt_requested
        and delivery.directed
        and not delivery.decision.delivered
        and bool(delivery.recipients)
    )


async def _send_receipt(request: ChatDeliveryRequest, delivery: _LocalDelivery) -> None:
    hub = request.hub
    if hub.journal is not None and delivery.directed and delivery.message_seq is not None:
        await commit_delivery_receipt_verdict(
            hub,
            request.websocket,
            sender=request.sender,
            target=delivery.target,
            msg_id=int(request.data["msg_id"]),
            message_seq=delivery.message_seq,
            decision=delivery.decision,
            directed=delivery.directed,
            client_msg_id=request.client_msg_id,
        )
    else:
        await send_and_track_delivery_receipt(
            hub,
            request.websocket,
            sender=request.sender,
            target=delivery.target,
            msg_id=int(request.data["msg_id"]),
            message_seq=delivery.message_seq,
            decision=delivery.decision,
            directed=delivery.directed,
            client_msg_id=request.client_msg_id,
        )


async def _fanout(request: ChatDeliveryRequest, delivery: _LocalDelivery) -> frozenset[str]:
    hub = request.hub
    if hub.private_directed_messages and delivery.directed:
        # Recipients, their receiver sidecars and granted observers are the audience.
        # Relay and journal retain visibility without broadcasting to every socket.
        audience = _directed_audience(
            delivery.recipients, hub.observing_identities(delivery.target)
        )
        return await hub.broadcast_directed(
            request.data, names=audience, sender_socket=request.websocket
        )
    return await hub.broadcast(request.data)


async def _delivery_feedback(request: ChatDeliveryRequest, delivery: _LocalDelivery) -> None:
    if delivery.escalation is not None:
        target, count, sender = delivery.escalation
        await request.escalate(target=target, count=count, sender=sender)
    if request.hub.warn_stale_recipients and delivery.directed and delivery.recipients:
        await warn_stale_recipients(
            request.hub,
            request.websocket,
            sender=request.sender,
            target=delivery.target,
            msg_id=int(request.data["msg_id"]),
            decision=delivery.decision,
        )
    if delivery.receipt_requested and not delivery.receipt_before_fanout:
        await _send_receipt(request, delivery)


async def deliver_local_chat(request: ChatDeliveryRequest) -> tuple[DeliveryLiveness, bool]:
    """Deliver a local chat with durable feedback before any uncertain fanout.

    Parameters
    ----------
    request
        Already admitted/stamped chat and the routing hub's escalation callback.

    Returns
    -------
    tuple[DeliveryLiveness, bool]
        Successful-write verdict and whether the chat was directed.
    """
    delivery = _resolve_audience(request)
    if not delivery.decision.delivered and delivery.directed:
        # Remember the blackhole even with a stale socket open: transport presence
        # cannot hide a durable message that reached nobody proven consume-live.
        delivery.escalation = _record_blackhole(request, delivery)
    _retain_chat(request, delivery)
    if delivery.receipt_before_fanout:
        await _send_receipt(request, delivery)
    successful = await _fanout(request, delivery)
    previous_delivery = delivery.decision.delivered
    delivery.decision = classify_completed_delivery(delivery.decision, successful)
    if delivery.directed and previous_delivery and not delivery.decision.delivered:
        delivery.escalation = _record_blackhole(request, delivery)
    if request.remember and delivery.decision.delivered:
        # Failed writes stay retryable; observer/sender echoes never prove delivery.
        request.hub.chat_dedupe.remember(
            request.sender,
            request.client_msg_id,
            request.digest,
            request.data,
            accepted_at=request.accepted_at,
        )
    await _delivery_feedback(request, delivery)
    return delivery.decision, delivery.directed


def _directed_audience(recipients: list[str], observers: Iterable[str]) -> list[str]:
    """Return the live delivery names for a directed message under recipient routing.

    Each recipient is reached on its own socket, durable mailbox sidecar, and distinct
    pane bridge, and any granted observers are appended. The broadcaster deduplicates
    by socket, so an observer that is also a recipient, or a recipient with no live
    sidecar, is handled without special-casing here.
    """
    names: list[str] = []
    for name in recipients:
        names.append(name)
        names.extend(waiter_sidecar_names(name))
    names.extend(observers)
    return names


def _matching_online_recipients(
    target: str,
    sender: str,
    online_agents: list[str],
    roles_of: Callable[[str], tuple[str, ...]],
) -> list[str]:
    """Return online recipients reached by ``target``, excluding the sender socket.

    A recipient is reached when ``target`` addresses its name, its bare project, or
    one of the roles it holds (looked up through ``roles_of``) — so a directed message
    to a ``<project>/<role>`` resolves to whichever agents currently answer to it.
    A receive-only waiter socket (``<identity>-rx``) also proves the logical
    ``<identity>`` is reachable for wake delivery. The logical name is what senders
    address and what waiters filter on; the socket name is only transport plumbing.
    """
    recipients: set[str] = set()
    for name in online_agents:
        if name == sender:
            continue
        roles = roles_of(name)
        logical = waiter_owner(name)
        if logical != name:
            logical_roles = tuple(dict.fromkeys((*roles, *roles_of(logical))))
            if logical != sender and is_recipient(target, logical, roles=logical_roles):
                recipients.add(logical)
                continue
        if is_recipient(target, name, roles=roles):
            recipients.add(name)
    return sorted(recipients)
