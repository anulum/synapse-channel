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

A hub configured for two-person approval adds one more gate *after* the authorisation gate: an
authorised relay is not applied on its own, but recorded pending in the hub's
:class:`~synapse_channel.core.operator_relay_approval.RelayApprovalLedger` and answered ``pending``;
only a second, distinct verified federation principal submitting the same action carries it out.
Both opaque principal fingerprints and descriptive operator labels are audited, so aliases from
one mutually authenticated peer cannot manufacture a quorum.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.journal import (
    RELAY_DIRECTION_IN,
    record_operator_relay,
    record_operator_release,
)
from synapse_channel.core.operator_relay import RelayDecision, authorise_relay
from synapse_channel.core.operator_relay_approval import ApprovalOutcome, ApprovalStatus
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    RelayWireError,
    decode_relay_request,
    encode_relay_result,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.state_transaction import durable_state_transaction

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

logger = logging.getLogger(__name__)

RELAY_STATUS_APPLIED = "applied"
"""Audit status: the relay was carried out (single-operator, or the second-operator approval)."""

RELAY_STATUS_PENDING = "pending"
"""Audit status: the relay was recorded under two-person policy, awaiting a second operator."""

_PENDING_DETAIL = "recorded; awaiting approval by a second operator"
_AWAITING_DETAIL = "already recorded by this verified principal; awaiting a distinct principal"


@dataclass(frozen=True, slots=True)
class _RelayAuthorisation:
    """Relay-policy decision plus the verified peer principal that produced it."""

    decision: RelayDecision
    principal: str = ""


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

    authorisation = _authorise(hub, sender, request, websocket)
    decision = authorisation.decision
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
    if hub.require_two_person_relay:
        result = _apply_with_two_person(hub, sender, request, authorisation.principal)
    else:
        result = _apply_release(hub, sender, request)
    await _send_result(hub, websocket, sender, result)


def _authorise(
    hub: SynapseHub, sender: str, request: RelayActionRequest, websocket: Any
) -> _RelayAuthorisation:
    """Compose the peer, scope, and ownership gates into one relay authorisation decision.

    The peer gate reads the live certificate through the hub's serving policy; a hub with no
    policy authorises nothing. The bounded scope the policy returns and this hub's namespace
    ownership feed the deny-by-default policy in
    :func:`~synapse_channel.core.operator_relay.authorise_relay`.
    """
    ownership = hub.namespace_ownership
    owns_namespace = ownership is not None and ownership.resolve(request.namespace).grants_locally
    require_reason = hub.require_relay_reason
    policy = hub.multihub_serving_policy
    if policy is None:
        return _RelayAuthorisation(
            authorise_relay(
                request,
                peer_authorised=False,
                scope=(),
                owns_namespace=owns_namespace,
                require_reason=require_reason,
            )
        )
    authorisation = policy.authorise(sender=sender, websocket=websocket)
    peer_authorised = authorisation.allowed and bool(authorisation.principal)
    return _RelayAuthorisation(
        authorise_relay(
            request,
            peer_authorised=peer_authorised,
            scope=authorisation.scope,
            owns_namespace=owns_namespace,
            require_reason=require_reason,
        ),
        principal=authorisation.principal if peer_authorised else "",
    )


def _apply_with_two_person(
    hub: SynapseHub, sender: str, request: RelayActionRequest, principal: str
) -> RelayActionResult:
    """Apply a relay only once a second, distinct verified principal has approved it.

    The already-authorised request and its verified peer principal are submitted to the hub's
    approval ledger. A second, different principal completing the quorum applies the release; a
    first request or an alias from the same principal remains pending and is audited as such.
    """
    outcome = hub.relay_approvals.submit(request, principal=principal)
    if outcome.status is ApprovalStatus.APPROVED:
        return _apply_release(
            hub,
            sender,
            request,
            requester=outcome.requester,
            approver=outcome.approver,
            requester_principal=outcome.requester_principal,
            approver_principal=outcome.approver_principal,
        )
    _audit_pending(hub, sender, request, outcome)
    detail = _PENDING_DETAIL if outcome.status is ApprovalStatus.RECORDED else _AWAITING_DETAIL
    return RelayActionResult(
        applied=False,
        action=request.action,
        namespace=request.namespace,
        task_id=request.task_id,
        owner_hub_id=hub.hub_id,
        detail=detail,
        pending=True,
    )


def _apply_release(
    hub: SynapseHub,
    sender: str,
    request: RelayActionRequest,
    *,
    requester: str = "",
    approver: str = "",
    requester_principal: str = "",
    approver_principal: str = "",
) -> RelayActionResult:
    """Force-release the targeted lease, audit the relay, and notify this hub's agents.

    The previous holder is read before the release so it can be named in the audit and the
    notice. On success the release is journalled twice — a standard ``release`` for state
    reconstruction and an ``operator_relay`` for cross-hub provenance — and this hub's agents
    are told the lease was revoked. A task that is not claimed is a no-op: the relay was
    authorised but there was nothing to release, so it is reported unapplied and not journalled.
    Under two-person approval ``approver`` names the second operator whose approval carried it out,
    empty for a single-operator relay.
    """
    task_id = request.task_id.strip()
    existing = hub.state.claims.get(task_id)
    previous_owner = existing.owner if existing is not None else ""
    with durable_state_transaction(hub.state, task_id, enabled=hub.journal is not None):
        applied, detail = hub.state.force_release(request.task_id, by=request.operator)
        if applied and hub.journal is not None:
            record_operator_release(
                hub.journal,
                task_id,
                {
                    "action": request.action,
                    "namespace": request.namespace,
                    "task_id": task_id,
                    "direction": RELAY_DIRECTION_IN,
                    "status": RELAY_STATUS_APPLIED,
                    "peer": sender,
                    "operator": request.operator,
                    "requester": requester,
                    "approver": approver,
                    "requester_principal": requester_principal,
                    "approver_principal": approver_principal,
                    "origin_hub_id": request.origin_hub_id,
                    "reason": request.reason,
                    "break_glass": request.break_glass,
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


def _audit_pending(
    hub: SynapseHub, sender: str, request: RelayActionRequest, outcome: ApprovalOutcome
) -> None:
    """Record an audit event for a relay recorded pending a second operator's approval.

    The pending request is journalled as an audit-only ``operator_relay`` event with a
    ``pending`` status and ``applied`` false — so the durable log shows who asked for a governed
    action before a second operator carried it out, completing the two-person trail. Nothing is
    released, so no ``release`` event is written.
    """
    if hub.journal is None:
        return
    record_operator_relay(
        hub.journal,
        {
            "action": request.action,
            "namespace": request.namespace,
            "task_id": request.task_id.strip(),
            "direction": RELAY_DIRECTION_IN,
            "status": RELAY_STATUS_PENDING,
            "peer": sender,
            "operator": request.operator,
            "requester": outcome.requester,
            "requester_principal": outcome.requester_principal,
            "origin_hub_id": request.origin_hub_id,
            "reason": request.reason,
            "break_glass": request.break_glass,
            "applied": False,
            "detail": _PENDING_DETAIL,
        },
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
