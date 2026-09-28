# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serving half of cross-hub messaging on the hub that hosts the target
"""Serving half of cross-hub messaging: act on a peer's forward for a local seat.

A peer hub sends one :data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_MESSAGE_FORWARD`
and receives one :data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_MESSAGE_RESULT`.
Accepting a forward puts another host's message into a local mailbox or delivery session, so
every step fails closed:

* the peer must hold a grant in this hub's
  :class:`~synapse_channel.core.multihub_serving.MultiHubServingPolicy` and present its pinned
  client certificate on the live connection; a hub without a policy accepts no forward;
* the peer's federation peering must list the **target's project namespace**; a roster request
  sees only seats in listed namespaces;
* the sender is presented as ``seat@origin_hub``, with ``origin_hub`` taken from the
  authenticated connection and never from the frame, so it can never pass for a local seat
  (local names may not contain ``@``) and never gains task control (``interrupt``/``steer``);
* a hub whose startup replay quarantined corrupt journal rows refuses every forward that
  would append durable state, exactly as it refuses local mutations; roster and status
  reads stay available;
* ``(origin_hub, forward_id)`` is answered once: a retry replays the stored answer, and a
  reused id with different content is refused.

A chat goes through the same router as a local chat
(:func:`~synapse_channel.core.handlers.messaging.route_chat`), charged to the peer
connection's quota bucket, so mailbox, private routing and dead-letter behaviour are identical.
Its answer carries the live-recipient verdict the sender's receipt is built from.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.handlers.delivery_modes import (
    admit_delivery_request,
    cancel_delivery,
    delivery_status_frame,
    require_delivery_profile,
)
from synapse_channel.core.handlers.messaging import route_chat
from synapse_channel.core.hub_address import federated_sender, is_single_seat, is_valid_hub_id
from synapse_channel.core.message_forward_ledger import request_digest
from synapse_channel.core.message_forward_wire import (
    MessageForwardRequest,
    MessageForwardResult,
    MessageForwardWireError,
    decode_message_forward_request,
    encode_message_forward_request,
    encode_message_forward_result,
)
from synapse_channel.core.multihub_serving import MultiHubServingPolicy
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

logger = logging.getLogger("synapse.message_forward")


class _Refused(Exception):
    """Internal signal carrying a refusal code and detail to the result."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


async def handle_multihub_message_forward(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Act on one forward from an authenticated peer hub and answer it once.

    Parameters
    ----------
    hub : SynapseHub
        The hub hosting the target seat.
    sender : str
        The forwarding peer's registered id; this is the authenticated origin hub.
    data : dict[str, Any]
        The forward frame.
    websocket : Any
        The peer's connection; the single result is sent back on it.
    """
    try:
        request = decode_message_forward_request(data)
    except MessageForwardWireError as exc:
        logger.warning("Refused malformed message forward from peer %r: %s", sender, exc)
        await hub._send_json(
            websocket,
            hub._system(
                "Malformed multi-hub message forward", msg_type=MessageType.ERROR, target=sender
            ),
        )
        return
    digest = request_digest(encode_message_forward_request(request))
    ledger = hub.message_forward_ledger
    stored = ledger.inbound(sender, request.forward_id)
    if stored is not None and stored.digest == digest:
        answer = dict(stored.result)
        answer["disposition"] = "duplicate"
        await _send(hub, websocket, sender, answer)
        return
    if stored is not None:
        result = _refusal(hub, request, "forward_id_conflict", "forward id reused with new content")
    else:
        try:
            payload = await _act(hub, sender, request, websocket)
            result = MessageForwardResult(
                forward_id=request.forward_id,
                disposition="accepted",
                answering_hub=hub.hub_id,
                result=payload,
            )
        except _Refused as refusal:
            result = _refusal(hub, request, refusal.code, refusal.detail)
    encoded = encode_message_forward_result(result)
    # Only accepted answers are remembered: a refusal caused by quota or a missing grant
    # must not become final for a retry sent after the cause has cleared.
    if result.disposition == "accepted":
        ledger.record_inbound(
            sender, request.forward_id, digest=digest, result=encoded, now=time.time()
        )
    await _send(hub, websocket, sender, encoded)


def _refusal(
    hub: SynapseHub, request: MessageForwardRequest, code: str, detail: str
) -> MessageForwardResult:
    """Build a refusal result."""
    return MessageForwardResult(
        forward_id=request.forward_id,
        disposition="refused",
        answering_hub=hub.hub_id,
        reason_code=code,
        detail=detail[:512],
    )


async def _send(hub: SynapseHub, websocket: Any, peer: str, fields: dict[str, Any]) -> None:
    """Send one private result frame back to the forwarding peer."""
    await hub._send_json(
        websocket,
        hub._system(
            "Multi-hub message result",
            msg_type=MessageType.MULTIHUB_MESSAGE_RESULT,
            target=peer,
            **fields,
        ),
    )


def _authorise(hub: SynapseHub, peer: str, websocket: Any, namespace: str) -> None:
    """Refuse unless the serving policy lets ``peer`` address ``namespace`` right now."""
    policy = hub.multihub_serving_policy
    if policy is None:
        raise _Refused("peer_not_authorised", "this hub accepts no forwarded messages")
    if not namespace:
        raise _Refused("namespace_not_granted", "target has no project namespace")
    decision = policy.authorise_namespace(sender=peer, websocket=websocket, namespace=namespace)
    if not decision.allowed:
        raise _Refused(
            "namespace_not_granted", f"peer may not address {namespace}: {decision.reason}"
        )


async def _act(
    hub: SynapseHub, peer: str, request: MessageForwardRequest, websocket: Any
) -> dict[str, Any]:
    """Perform one authorised forward and return its result payload."""
    # Checked first: the origin id becomes part of every name built below, and a peer
    # registered under a seat-shaped or otherwise invalid id is refused, never crashed on.
    if not is_valid_hub_id(peer):
        raise _Refused("invalid_origin", "forwarding peer id is not a valid hub id")
    if request.kind == "who":
        return _roster(hub, peer, websocket, _authorise_connection(hub, peer, websocket))
    sender = federated_sender(request.sender_seat, peer)
    if request.kind in ("chat", "delivery_request") and not is_single_seat(request.target_seat):
        # The origin refuses these too; a modified peer must not widen a forward past the
        # namespace it was authorised for, by a comma list or a glob.
        raise _Refused("invalid_target", "a forwarded target must name exactly one seat")
    if request.kind == "chat":
        _authorise(hub, peer, websocket, project_of(request.target_seat))
        _refuse_while_degraded(hub)
        return await _deliver_chat(hub, peer, sender, request, websocket)
    data = dict(request.body)
    try:
        ledger = require_delivery_profile(hub, data)
        if request.kind == "delivery_request":
            _authorise(hub, peer, websocket, project_of(request.target_seat))
            _refuse_while_degraded(hub)
            data["target"] = request.target_seat
            admission = await admit_delivery_request(hub, sender=sender, origin_hub=peer, data=data)
            # A deadline that elapses during admission is expired by the hub's delivery sweep
            # (every five seconds) or on the next status read. Unlike the local path, the
            # requester has no socket on this hub to be told sooner, so no inline sweep runs.
            return {"status": admission.status}
        record = ledger.get(str(data.get("operation_key") or ""))
        if record is None:
            raise _Refused("unknown_request", "delivery request does not exist")
        _authorise(hub, peer, websocket, project_of(str(record.request["target"])))
        if request.kind == "delivery_status":
            return {"status": await delivery_status_frame(hub, requester=sender, data=data)}
        _refuse_while_degraded(hub)
        return {"status": await cancel_delivery(hub, requester=sender, data=data)}
    except DeliveryRefusal as refusal:
        raise _Refused(refusal.code, str(refusal)) from refusal


def _refuse_while_degraded(hub: SynapseHub) -> None:
    """Refuse an authorised forward that appends durable state while replay is incomplete.

    This is the rule the journal recovery gate applies to local mutations. It is checked
    after authorisation, so an unauthorised peer never learns the hub's recovery state.
    """
    if hub.journal_corrupt_rows:
        raise _Refused("journal_recovery_required", "durable journal recovery is required")


def _authorise_connection(hub: SynapseHub, peer: str, websocket: Any) -> MultiHubServingPolicy:
    """Return the serving policy when it authorises ``peer`` at all; refuse otherwise."""
    policy = hub.multihub_serving_policy
    if policy is None or not policy.authorise(sender=peer, websocket=websocket).allowed:
        raise _Refused("peer_not_authorised", "this hub does not serve this peer")
    return policy


def _roster(
    hub: SynapseHub, peer: str, websocket: Any, policy: MultiHubServingPolicy
) -> dict[str, Any]:
    """Return the online seats and delivery sessions in namespaces the peer may address."""
    granted: dict[str, bool] = {}

    def visible(name: str) -> bool:
        namespace = project_of(name)
        if not namespace:
            return False
        if namespace not in granted:
            granted[namespace] = policy.authorise_namespace(
                sender=peer, websocket=websocket, namespace=namespace
            ).allowed
        return granted[namespace]

    online = [name for name in hub.online_agents() if visible(name)]
    sessions: dict[str, Any] = {}
    for name in online:
        session = hub.clients.delivery_session(name)
        if session is not None:
            sessions[name] = {
                "incarnation": session.incarnation,
                "capabilities": dict(session.capabilities),
            }
    return {"online_agents": online, "delivery_sessions": sessions}


async def _deliver_chat(
    hub: SynapseHub, peer: str, sender: str, request: MessageForwardRequest, websocket: Any
) -> dict[str, Any]:
    """Route a forwarded chat locally and return its live-recipient verdict."""
    body = request.body
    payload = body.get("payload")
    if not isinstance(payload, str):
        raise _Refused("invalid_shape", "chat payload must be a string")
    chat: dict[str, Any] = {
        "type": MessageType.CHAT,
        "sender": sender,
        "target": request.target_seat,
        "payload": payload,
        "forwarded_from": peer,
        "forward_id": request.forward_id,
    }
    client_msg_id = body.get("client_msg_id")
    if isinstance(client_msg_id, str) and client_msg_id:
        chat["client_msg_id"] = client_msg_id
    routing = await route_chat(hub, sender, chat, websocket, report_refusal=False)
    if routing.refusal:
        raise _Refused("chat_refused", routing.refusal)
    # The wire codec forbids '@' in the target seat and the frame carries no channel, so an
    # accepted forwarded chat is always routed to local seats and has a verdict.
    delivery = routing.verdict
    return {
        "seq": int(chat["seq"]) if "seq" in chat else None,
        "msg_id": int(chat["msg_id"]),
        "delivered": delivery.delivered,
        "recipients": list(delivery.live_recipients),
        "matched_recipients": list(delivery.matched_recipients),
        "stale_recipients": list(delivery.stale_recipients),
        "reason": delivery.reason,
        "dead_lettered": routing.directed and not delivery.delivered,
    }
