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
* a hub whose startup replay quarantined corrupt journal rows refuses, exactly as its journal
  recovery gate refuses local mutations;
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

from synapse_channel.core.claim_holder_presence import release_abandoned_claims
from synapse_channel.core.federation_serving import authorise_federated_mutation, claim_namespace
from synapse_channel.core.handlers.leasing import apply_claim_async, claim_grant_fields
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_claim_wire import (
    ClaimForwardRequest,
    ClaimForwardResult,
    ClaimWireError,
    decode_claim_forward_request,
    encode_claim_forward_result,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import Protocol

    from synapse_channel.core.event_row_recovery import CorruptEventRow
    from synapse_channel.core.handlers.leasing import LeasingContext
    from synapse_channel.core.multihub_serving import MultiHubServingPolicy
    from synapse_channel.core.namespace_ownership import NamespaceOwnership
    from synapse_channel.core.state_models import TaskClaim

    class MultihubClaimContext(LeasingContext, Protocol):
        """Capabilities consumed by multihub claim handlers and their callees."""

        @property
        def journal_corrupt_rows(self) -> tuple[CorruptEventRow, ...]:
            """Return the journal corrupt rows used by this handler family."""
            ...

        @property
        def multihub_serving_policy(self) -> MultiHubServingPolicy | None:
            """Return the multihub serving policy used by this handler family."""
            ...

        @property
        def namespace_ownership(self) -> NamespaceOwnership | None:
            """Return the namespace ownership used by this handler family."""
            ...

        @property
        def observed_asserting_hubs(self) -> Callable[[str], Iterable[str]] | None:
            """Return the feed of hub ids observed asserting authority over a namespace."""
            ...


logger = logging.getLogger(__name__)


async def handle_multihub_claim_request(
    hub: MultihubClaimContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Grant a forwarded claim authoritatively and relay the verdict, or refuse fail-closed.

    Parameters
    ----------
    hub : MultihubClaimContext
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
        await hub.send_json(
            websocket,
            hub.system(
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

    await release_abandoned_claims(hub)
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
        await hub.broadcast(
            hub.system(application.message, msg_type=MessageType.CLAIM_GRANTED, **grant_fields)
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


def _duplicate_forwarded_claim(
    hub: MultihubClaimContext, request: ClaimForwardRequest
) -> TaskClaim | None:
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
    hub: MultihubClaimContext, sender: str, request: ClaimForwardRequest, websocket: Any
) -> ClaimForwardResult | None:
    """Return a denial result when the peer or this hub may not grant the claim, else ``None``.

    The fail-closed gates a forwarded claim must clear before it is applied, in order: the
    peer is trusted for the request's own namespace
    (:func:`~synapse_channel.core.federation_serving.authorise_federated_mutation`), this
    hub's journal does not need recovery, the peering's scope grants ``claim`` in the
    namespace, this hub owns the namespace uncontested, and the claim is bound to the request
    (:func:`_unbound`). The recovery state is checked after the peer, so an unauthorised peer
    never learns it. A refusal names this hub so the forwarding side knows who answered.
    """
    serving = authorise_federated_mutation(
        policy=hub.multihub_serving_policy,
        ownership=hub.namespace_ownership,
        observed_asserting_hubs=hub.observed_asserting_hubs,
        sender=sender,
        websocket=websocket,
        namespace=request.namespace,
        verb="claim",
    )
    detail: str | None
    if not serving.peer_authorised:
        detail = "peer not authorised to forward claims"
    elif hub.journal_corrupt_rows:
        detail = "durable journal recovery is required"
    elif not serving.scope_granted:
        detail = f"peering scope does not grant claim in namespace {request.namespace!r}"
    elif not serving.namespace_owned:
        detail = f"this hub does not own namespace {request.namespace!r}"
    else:
        detail = _unbound(hub, request)
    if detail is None:
        return None
    logger.warning("Refused multi-hub claim from peer %r: %s", sender, detail)
    return ClaimForwardResult(
        granted=False,
        task_id=request.task_id,
        namespace=request.namespace,
        owner_hub_id=hub.hub_id,
        detail=detail,
    )


def _unbound(hub: MultihubClaimContext, request: ClaimForwardRequest) -> str | None:
    """Return why a forwarded claim does not bind to its stated namespace and task, or ``None``.

    The claim body must claim the request's own task, the claimant must be a seat of the
    request's namespace, and a live claim on that task held in another namespace is not
    the peer's to contend for.
    """
    body_task = str(request.claim.get("task_id") or "").strip()
    if body_task != request.task_id:
        return "claim body names another task than the request"
    if claim_namespace(request.claimant) != request.namespace:
        return f"claimant is not a seat of namespace {request.namespace!r}"
    existing = hub.state.claims.get(request.task_id)
    if existing is not None and claim_namespace(existing.owner) != request.namespace:
        return f"task is held in another namespace than {request.namespace!r}"
    return None


async def _send_result(
    hub: MultihubClaimContext, websocket: Any, sender: str, result: ClaimForwardResult
) -> None:
    """Send one private claim-forward result back to the forwarding peer."""
    await hub.send_json(
        websocket,
        hub.system(
            "Multi-hub claim result",
            msg_type=MessageType.MULTIHUB_CLAIM_RESULT,
            target=sender,
            **encode_claim_forward_result(result),
        ),
    )


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.MULTIHUB_CLAIM_REQUEST,),
        handler=handle_multihub_claim_request,
        reply_types=(
            MessageType.MULTIHUB_CLAIM_RESULT,
            MessageType.CLAIM_GRANTED,
            MessageType.RELEASE_GRANTED,
        ),
        mutates=True,
        replay_protected=False,
        mutation_guarded=False,
        accesses=None,
        event_kinds=(
            EventKind.CLAIM,
            EventKind.CLAIM_DENIAL,
            EventKind.RELEASE,
        ),
        minimum_wire_version=1,
        commands=(),
    ),
)
