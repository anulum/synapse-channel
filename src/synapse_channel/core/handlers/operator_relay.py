# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serving half of a cross-hub operator relay (apply on the acting hub)
"""Serving half of a cross-hub operator relay: apply a governed action on the acting hub.

When an operator relays a governed action to a peer hub over the federation transport, that
hub receives an :data:`~synapse_channel.core.protocol.MessageType.OPERATOR_RELAY_REQUEST` and
answers with a single :data:`~synapse_channel.core.protocol.MessageType.OPERATOR_RELAY_RESULT`,
both framed by the shared codec (:mod:`synapse_channel.core.operator_relay_wire`) so the
initiating side and this serving side agree on the format without importing each other.

A relay *mutates* this hub's state on a remote operator's behalf, so the gate is strict and
fails closed at every step, exactly like a forwarded claim:

* the peer must be authorised by the hub's
  :class:`~synapse_channel.core.multihub_serving.MultiHubServingPolicy` — a hub with no policy
  accepts no relay at all, since a remote mutation from an unauthenticated peer is never the
  safe default;
* the action must be in the deny-by-default :data:`~synapse_channel.core.operator_relay.
  RELAYABLE_ACTIONS` registry, the peering's bounded scope must grant the action's verb in the
  namespace, and this hub must authoritatively own the namespace
  (:func:`~synapse_channel.core.operator_relay.authorise_relay` composes all three);
* a malformed request is answered with an error frame and applies nothing.

An applied release is journalled twice: a standard ``release`` event keeps state
reconstruction correct across a restart, and an audit-only ``operator_relay`` event records
the cross-hub provenance — the verified peer, the asserted operator and origin hub, and the
previous holder — that a plain release never carries. The hub's own agents are then told the
lease was revoked, so a former holder does not keep acting on a dropped lease.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from synapse_channel.core.journal import (
    RELAY_DIRECTION_IN,
    record_operator_relay,
    record_release,
)
from synapse_channel.core.operator_relay import RelayDecision, authorise_relay
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    RelayWireError,
    decode_relay_request,
    encode_relay_result,
)
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

logger = logging.getLogger(__name__)


async def handle_operator_relay_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Apply a relayed operator action behind the deny-by-default gate, or refuse fail-closed.

    Parameters
    ----------
    hub : SynapseHub
        The acting hub whose state the action mutates; ``hub.hub_id`` stamps the result.
    sender : str
        The relaying peer hub; the result is addressed privately to it, and the serving
        policy authorises the relay against this cryptographically verified identity.
    data : dict[str, Any]
        The request frame, decoded by the shared codec. A body the codec rejects is answered
        with an error frame.
    websocket : Any
        The peer socket the result is sent back on.
    """
    try:
        request = decode_relay_request(data)
    except RelayWireError:
        logger.warning("Refused malformed operator relay request from peer %r", sender)
        await hub._send_json(
            websocket,
            hub._system(
                "Malformed operator relay request",
                msg_type=MessageType.ERROR,
                target=sender,
            ),
        )
        return

    decision = _authorise(hub, sender, request, websocket)
    if not decision.allowed:
        logger.warning(
            "Refused operator relay %r from peer %r in namespace %r: %s",
            request.action,
            sender,
            request.namespace,
            decision.reason,
        )
        await _send_result(
            hub,
            websocket,
            sender,
            RelayActionResult(
                applied=False,
                action=request.action,
                namespace=request.namespace,
                task_id=request.task_id,
                owner_hub_id=hub.hub_id,
                detail=decision.reason,
            ),
        )
        return

    # An allow decision guarantees the action is registered; the sole registered action is a
    # force-release, so applying it here is exhaustive for this slice.
    result = _apply_release(hub, sender, request)
    await _send_result(hub, websocket, sender, result)


def _authorise(
    hub: SynapseHub, sender: str, request: RelayActionRequest, websocket: Any
) -> RelayDecision:
    """Compose the peer, scope, and ownership gates into one relay authorisation decision.

    The peer gate reads the live certificate through the hub's serving policy; a hub with no
    policy authorises nothing. The bounded scope the policy returns and this hub's namespace
    ownership feed the deny-by-default policy in
    :func:`~synapse_channel.core.operator_relay.authorise_relay`.
    """
    ownership = hub.namespace_ownership
    owns_namespace = ownership is not None and ownership.resolve(request.namespace).grants_locally
    policy = hub.multihub_serving_policy
    if policy is None:
        return authorise_relay(
            request, peer_authorised=False, scope=(), owns_namespace=owns_namespace
        )
    authorisation = policy.authorise(sender=sender, websocket=websocket)
    return authorise_relay(
        request,
        peer_authorised=authorisation.allowed,
        scope=authorisation.scope,
        owns_namespace=owns_namespace,
    )


def _apply_release(hub: SynapseHub, sender: str, request: RelayActionRequest) -> RelayActionResult:
    """Force-release the targeted lease, audit the relay, and notify this hub's agents.

    The previous holder is read before the release so it can be named in the audit and the
    notice. On success the release is journalled twice — a standard ``release`` for state
    reconstruction and an ``operator_relay`` for cross-hub provenance — and this hub's agents
    are told the lease was revoked. A task that is not claimed is a no-op: the relay was
    authorised but there was nothing to release, so it is reported unapplied and not journalled.
    """
    existing = hub.state.claims.get(request.task_id.strip())
    previous_owner = existing.owner if existing is not None else ""
    applied, detail = hub.state.force_release(request.task_id, by=request.operator)
    if applied and hub.journal is not None:
        record_release(hub.journal, request.task_id.strip())
        record_operator_relay(
            hub.journal,
            {
                "action": request.action,
                "namespace": request.namespace,
                "task_id": request.task_id.strip(),
                "direction": RELAY_DIRECTION_IN,
                "peer": sender,
                "operator": request.operator,
                "origin_hub_id": request.origin_hub_id,
                "previous_owner": previous_owner,
                "applied": True,
                "detail": detail,
            },
        )
    return RelayActionResult(
        applied=applied,
        action=request.action,
        namespace=request.namespace,
        task_id=request.task_id,
        owner_hub_id=hub.hub_id,
        detail=detail,
    )


async def _send_result(
    hub: SynapseHub, websocket: Any, sender: str, result: RelayActionResult
) -> None:
    """Send one private operator-relay result back to the relaying peer.

    On an applied release, this hub's own agents are told first — a broadcast naming the
    revoked task and the operator — so a former holder learns its lease is gone rather than
    discovering it only on its next failed action.
    """
    if result.applied:
        await hub._broadcast(
            hub._system(
                f"Task {result.task_id!r} in {result.namespace!r} was released by operator "
                f"relay: {result.detail}",
                msg_type=MessageType.RELEASE_GRANTED,
                task_id=result.task_id,
            )
        )
    await hub._send_json(
        websocket,
        hub._system(
            "Operator relay result",
            msg_type=MessageType.OPERATOR_RELAY_RESULT,
            target=sender,
            **encode_relay_result(result),
        ),
    )
