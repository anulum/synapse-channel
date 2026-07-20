# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serving half of cross-hub claim forwarding (grant on the owning hub)
"""Serving half of cross-hub claim forwarding: grant a routed claim on the owning hub.

Claims are mutual exclusion routed by namespace ownership
(:mod:`synapse_channel.core.namespace_ownership`): only the hub that owns a namespace grants
claims inside it. When an agent claims through a non-owning hub, that hub forwards the request
here with a :data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_CLAIM_REQUEST` frame; this
handler answers with a single private
:data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_CLAIM_RESULT` carrying the owning hub's
authoritative verdict, framed by the shared codec
(:mod:`synapse_channel.core.multihub_claim_wire`) so the forwarding half and this serving half
agree on the format without importing each other.

Unlike the read-only event-log pull, a forwarded claim *mutates* this hub's lease state on a
remote agent's behalf, so the gate is stricter and fails closed at every step:

* the peer must be authorised by the hub's
  :class:`~synapse_channel.core.multihub_serving.MultiHubServingPolicy` — a hub with no policy
  accepts no forwarded claim at all, since accepting a remote mutation from an unauthenticated
  peer is never the safe default;
* this hub must authoritatively and uncontestedly own the namespace, or it refuses rather than
  granting a claim another hub owns;
* a malformed request — which carries no namespace or claimant to grant under — is answered with
  an error frame and grants nothing.

A granted claim is applied through the one authoritative async grant core
(:func:`~synapse_channel.core.handlers.leasing.apply_claim_async`) and broadcast to this hub's own
agents, exactly as a direct claim is, then the same grant fields are relayed back so the
forwarding hub can hand its client an authentic ``CLAIM_GRANTED``.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from synapse_channel.core.handlers.leasing import apply_claim_async, claim_grant_fields
from synapse_channel.core.multihub_claim_wire import (
    ClaimForwardRequest,
    ClaimForwardResult,
    ClaimWireError,
    decode_claim_forward_request,
    encode_claim_forward_result,
)
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub
    from synapse_channel.core.state_models import TaskClaim

logger = logging.getLogger(__name__)


async def handle_multihub_claim_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Grant a forwarded claim authoritatively and relay the verdict, or refuse fail-closed.

    Parameters
    ----------
    hub : SynapseHub
        The owning hub whose lease state the claim is applied to; ``hub.hub_id`` stamps the
        result so the forwarding hub knows which hub answered.
    sender : str
        The forwarding peer hub; the result is addressed privately to it.
    data : dict[str, Any]
        The request frame, decoded by the shared codec into a namespace, claimant, task id,
        and the original claim body. A body the codec rejects is answered with an error frame.
    websocket : Any
        The peer socket the result is sent back on.
    """
    try:
        request = decode_claim_forward_request(data)
    except ClaimWireError:
        logger.warning("Refused malformed multi-hub claim request from peer %r", sender)
        await hub._send_json(
            websocket,
            hub._system(
                "Malformed multi-hub claim request",
                msg_type=MessageType.ERROR,
                target=sender,
            ),
        )
        return

    refusal = _refuse_claim(hub, sender, request, websocket)
    if refusal is not None:
        await _send_result(hub, websocket, sender, refusal)
        return

    duplicate = _duplicate_forwarded_claim(hub, request)
    if duplicate is not None:
        await _send_result(
            hub,
            websocket,
            sender,
            ClaimForwardResult(
                granted=True,
                task_id=request.task_id,
                namespace=request.namespace,
                owner_hub_id=hub.hub_id,
                detail=f"Task '{request.task_id}' already claimed by {request.claimant}.",
                grant=claim_grant_fields(duplicate),
            ),
        )
        return

    # The serving policy has authenticated the peer, but the nested claimant is
    # still a peer assertion. Charge every alias forwarded by one peer to that
    # peer's stable bucket so name rotation cannot multiply the owning hub's cap.
    application = await apply_claim_async(
        hub,
        request.claimant,
        request.claim,
        quota_principal=f"federation-peer:{sender}",
    )
    if application.claim is not None:
        hub.counters.claims_granted += 1
        grant_fields = claim_grant_fields(application.claim)
        # The lease now authoritatively exists on this hub: tell its own agents, exactly as
        # a direct claim does, before relaying the grant back to the forwarding hub.
        await hub._broadcast(
            hub._system(application.message, msg_type=MessageType.CLAIM_GRANTED, **grant_fields)
        )
        result = ClaimForwardResult(
            granted=True,
            task_id=request.task_id,
            namespace=request.namespace,
            owner_hub_id=hub.hub_id,
            detail=application.message,
            grant=grant_fields,
        )
    else:
        result = ClaimForwardResult(
            granted=False,
            task_id=request.task_id,
            namespace=request.namespace,
            owner_hub_id=hub.hub_id,
            detail=application.message,
        )
    await _send_result(hub, websocket, sender, result)


def _duplicate_forwarded_claim(hub: SynapseHub, request: ClaimForwardRequest) -> TaskClaim | None:
    """Return the live existing lease for a duplicate forwarded claim, if present.

    The idempotency key for a forwarded grant is ``(task_id, claimant)``. If a peer retries
    the same forward after losing the result frame, the owning hub relays the existing lease
    without renewing it, rebroadcasting it, journalling another claim, or incrementing the
    owner grant counter. A stale lease is not considered duplicate; the normal grant path may
    reclaim it.
    """
    existing = hub.state.claims.get(request.task_id)
    if existing is None or existing.owner != request.claimant:
        return None
    if existing.lease_expires_at <= time.time():
        return None
    return existing


def _refuse_claim(
    hub: SynapseHub, sender: str, request: ClaimForwardRequest, websocket: Any
) -> ClaimForwardResult | None:
    """Return a denial result when the peer or this hub may not grant the claim, else ``None``.

    The two fail-closed gates a forwarded claim must clear before it is applied: the peer must
    be an authorised federated hub, and this hub must authoritatively own the namespace. A
    refusal names this hub so the forwarding side knows who answered.
    """
    if not _peer_authorised(hub, sender, websocket):
        logger.warning("Refused multi-hub claim from peer %r: peer not authorised", sender)
        return ClaimForwardResult(
            granted=False,
            task_id=request.task_id,
            namespace=request.namespace,
            owner_hub_id=hub.hub_id,
            detail="peer not authorised to forward claims",
        )
    if not _owns_namespace(hub, request.namespace):
        logger.warning(
            "Refused multi-hub claim from peer %r: hub does not own namespace %r",
            sender,
            request.namespace,
        )
        return ClaimForwardResult(
            granted=False,
            task_id=request.task_id,
            namespace=request.namespace,
            owner_hub_id=hub.hub_id,
            detail=f"this hub does not own namespace {request.namespace!r}",
        )
    return None


def _peer_authorised(hub: SynapseHub, sender: str, websocket: Any) -> bool:
    """Return whether the hub's serving policy permits ``sender`` to forward a claim.

    A forwarded claim mutates lease state, so the gate is stricter than the read-only log
    pull: a hub with no :class:`~synapse_channel.core.multihub_serving.MultiHubServingPolicy`
    refuses every forwarded claim rather than accepting an unauthenticated remote mutation.
    When a policy is configured, the decision is taken from the peer's live certificate.
    """
    policy = hub.multihub_serving_policy
    if policy is None:
        return False
    return policy.authorise(sender=sender, websocket=websocket).allowed


def _owns_namespace(hub: SynapseHub, namespace: str) -> bool:
    """Return whether this hub authoritatively and uncontestedly owns ``namespace``.

    A hub with no :class:`~synapse_channel.core.namespace_ownership.NamespaceOwnership` map
    owns nothing it could be forwarded a claim for, so it refuses; otherwise the namespace
    must resolve to a local, uncontested grant.
    """
    ownership = hub.namespace_ownership
    if ownership is None:
        return False
    return ownership.resolve(namespace).grants_locally


async def _send_result(
    hub: SynapseHub, websocket: Any, sender: str, result: ClaimForwardResult
) -> None:
    """Send one private claim-forward result back to the forwarding peer."""
    await hub._send_json(
        websocket,
        hub._system(
            "Multi-hub claim result",
            msg_type=MessageType.MULTIHUB_CLAIM_RESULT,
            target=sender,
            **encode_claim_forward_result(result),
        ),
    )
