# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — origin half of a cross-hub operator relay (route and forward from the origin)
"""Origin half of a cross-hub operator relay: route a relay this hub cannot apply to its owner.

The serving half (:mod:`synapse_channel.core.handlers.operator_relay`) applies a relayed action
on the hub that owns the target namespace; this is the origin half, which lets an operator target
their *own* hub and have it reach the owner on their behalf — so the operator never needs the
owning hub's credentials, exactly as a claim routes through the claimant's hub to the namespace's
owner.

When an :data:`~synapse_channel.core.protocol.MessageType.OPERATOR_RELAY_REQUEST` arrives, this
gate resolves the target namespace against the hub's ownership map and
(:mod:`synapse_channel.core.operator_relay_routing`) takes one of three routes, deny-by-default:

* **owned here** — the gate steps aside and the serving handler applies it, exactly as before;
* **owned by a peer with a relay route** — the gate forwards it over the federation transport,
  records an *outbound* audit event naming the local requester and the destination owner, and
  relays the owner's verdict back to the requester;
* **anything else** — an unrouted owner, an ungoverned namespace, or a partitioned one — is
  refused fail-closed with the reason, so a relay never reaches a hub the operator never armed.

The outbound audit is the origin-side half of a relay's two-hub trail: the owning hub records the
inbound event when it applies the action (:data:`~synapse_channel.core.journal.RELAY_DIRECTION_IN`),
and this hub records the outbound one (:data:`~synapse_channel.core.journal.RELAY_DIRECTION_OUT`),
so a force-release routed across hubs is attributable on both ends. The origin hub stamps its own
id as the forwarded request's ``origin_hub_id`` so the owner attributes the relay to the hub that
actually relayed it, never a value a local requester asserted.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from synapse_channel.core.journal import RELAY_DIRECTION_OUT, record_operator_relay
from synapse_channel.core.namespace_ownership import NamespaceOwnership, OwnershipDecision
from synapse_channel.core.operator_relay_routing import (
    RelayRouteKind,
    route_operator_relay,
)
from synapse_channel.core.operator_relay_transport import (
    OperatorRelayPeer,
    RelayForwarder,
    RelayTransportError,
)
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    RelayWireError,
    decode_relay_request,
    encode_relay_result,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

logger = logging.getLogger("synapse.hub")

_FORWARD_FAILED = "relay to the owning hub failed"
"""Detail reported to the requester when the forward never reached the owner's verdict."""


class OperatorRelayForwarding:
    """Route an inbound operator-relay frame: apply locally, forward to the owner, or refuse.

    The origin-side counterpart to
    :meth:`~synapse_channel.core.hub_frame_gates.HubFrameGates.forward_remote_claim`. It runs as
    a gate before dispatch: :meth:`route` returns whether the frame may proceed to the local
    serving handler (this hub owns the namespace) or was handled here (forwarded to the owner and
    the verdict relayed, or refused). Policy inputs are captured at construction — the hub never
    mutates them after ``__init__`` — and the hub's per-socket send, system-message factory, and
    durable journal enter as injected collaborators, the same callback-injection the other hub
    gates use.

    Parameters
    ----------
    namespace_ownership : NamespaceOwnership or None
        The single-authoritative-hub map that routes a relay by namespace ownership; ``None``
        leaves every relay to the local serving handler (single-hub behaviour, no forwarding).
    relay_peers : Mapping[str, OperatorRelayPeer] or None
        How to reach each owning hub to relay to it, keyed by owning hub id; ``None`` forwards
        nothing and a remote-owned relay is refused with the owner named.
    relay_forwarder : RelayForwarder
        The seam that relays a request to an owning hub over the network.
    observed_asserting_hubs : Callable[[str], Iterable[str]] or None
        Runtime feed of hub ids observed asserting authority over a namespace, folded into
        ownership resolution so a partition refuses the relay; ``None`` supplies no assertions.
    hub_id : str
        This hub's stable id, stamped as the forwarded relay's origin and sender.
    journal : EventStore or None
        The durable store the outbound audit event is written to; ``None`` records nothing.
    send_json : Callable[[Any, dict], Awaitable[None]]
        The hub's per-socket send (``hub._send_json``), used to reply to the requester.
    system : Callable[..., dict]
        The hub's system-message factory (``hub._system``), used to stamp each reply.
    """

    def __init__(
        self,
        *,
        namespace_ownership: NamespaceOwnership | None,
        relay_peers: Mapping[str, OperatorRelayPeer] | None,
        relay_forwarder: RelayForwarder,
        observed_asserting_hubs: Callable[[str], Iterable[str]] | None,
        hub_id: str,
        journal: EventStore | None,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
    ) -> None:
        self._namespace_ownership = namespace_ownership
        self._relay_peers = relay_peers
        self._relay_forwarder = relay_forwarder
        self._observed_asserting_hubs_feed = observed_asserting_hubs
        self._hub_id = hub_id
        self._journal = journal
        self._send_json = send_json
        self._system = system

    async def route(self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any) -> bool:
        """Route an operator-relay frame by namespace ownership before it is dispatched.

        Returns
        -------
        bool
            ``True`` when the frame may proceed to the local serving handler — this is not an
            operator-relay frame, no ownership map is configured, the target namespace is empty
            (left for the serving handler to reject as malformed), or this hub owns it. ``False``
            when the frame was handled here: forwarded to the owning hub and its verdict relayed,
            or refused fail-closed (a :data:`~synapse_channel.core.protocol.MessageType.
            OPERATOR_RELAY_RESULT` or error frame was sent).
        """
        if self._namespace_ownership is None or msg_type != MessageType.OPERATOR_RELAY_REQUEST:
            return True
        namespace = str(data.get("namespace") or "").strip()
        if not namespace:
            return True
        decision = self._namespace_ownership.resolve(
            namespace, asserting_hubs=self._observed_asserting_hubs(namespace)
        )
        route = route_operator_relay(decision, relay_peers=self._relay_peers)
        if route.kind is RelayRouteKind.APPLY_LOCAL:
            return True
        try:
            request = decode_relay_request(data)
        except RelayWireError:
            logger.warning("Refused malformed operator relay request from %r", sender)
            await self._send_json(
                websocket,
                self._system(
                    "Malformed operator relay request",
                    msg_type=MessageType.ERROR,
                    target=sender,
                ),
            )
            return False
        if route.kind is RelayRouteKind.FORWARD:
            assert route.peer is not None  # FORWARD always carries the resolved route
            result = await self._forward(sender, request, route.peer, decision)
        else:
            logger.warning(
                "Refused operator relay %r from %r in namespace %r: %s",
                request.action,
                sender,
                namespace,
                route.reason,
            )
            result = RelayActionResult(
                applied=False,
                action=request.action,
                namespace=request.namespace,
                task_id=request.task_id,
                owner_hub_id=decision.owner_hub_id or self._hub_id,
                detail=route.reason,
            )
        await self._reply(websocket, sender, result)
        return False

    async def _forward(
        self,
        sender: str,
        request: RelayActionRequest,
        peer: OperatorRelayPeer,
        decision: OwnershipDecision,
    ) -> RelayActionResult:
        """Relay a remote-owned action to its owner, audit the relay-out, and return the verdict.

        The forwarded request carries this hub's id as its ``origin_hub_id`` and sender, so the
        owner attributes the relay to the hub that relayed it and its serving policy authorises
        this hub as the relaying peer; the asserted ``operator`` is preserved. A transport
        failure fails closed as an unapplied result the requester can see, and either way the
        attempt is audited outbound so the origin-side log records the relay it sent.
        """
        forwarded = RelayActionRequest(
            action=request.action,
            namespace=request.namespace,
            task_id=request.task_id,
            operator=request.operator,
            origin_hub_id=self._hub_id,
            reason=request.reason,
            break_glass=request.break_glass,
        )
        try:
            result = await self._relay_forwarder(
                forwarded, uri=peer.uri, local_id=self._hub_id, token=peer.token
            )
        except RelayTransportError:
            logger.warning(
                "Relaying %r for %r to owner %s failed",
                request.action,
                sender,
                decision.owner_hub_id,
            )
            result = RelayActionResult(
                applied=False,
                action=request.action,
                namespace=request.namespace,
                task_id=request.task_id,
                owner_hub_id=decision.owner_hub_id or self._hub_id,
                detail=_FORWARD_FAILED,
            )
        self._audit_out(sender, request, decision, result)
        return result

    def _audit_out(
        self,
        sender: str,
        request: RelayActionRequest,
        decision: OwnershipDecision,
        result: RelayActionResult,
    ) -> None:
        """Record the outbound half of a relay's two-hub audit trail, if a journal is present.

        Names the local requester and the destination owner — the provenance the owner's inbound
        event cannot carry — with the same outcome the owner reported, so the two events reconcile.
        """
        if self._journal is None:
            return
        record_operator_relay(
            self._journal,
            {
                "action": request.action,
                "namespace": request.namespace,
                "task_id": request.task_id,
                "direction": RELAY_DIRECTION_OUT,
                "agent": sender,
                "operator": request.operator,
                "origin_hub_id": self._hub_id,
                "owner_hub_id": decision.owner_hub_id or "",
                "reason": request.reason,
                "break_glass": request.break_glass,
                "applied": result.applied,
                "detail": result.detail,
            },
        )

    async def _reply(self, websocket: Any, sender: str, result: RelayActionResult) -> None:
        """Send one private operator-relay result back to the requester on its own socket."""
        await self._send_json(
            websocket,
            self._system(
                "Operator relay result",
                msg_type=MessageType.OPERATOR_RELAY_RESULT,
                target=sender,
                **encode_relay_result(result),
            ),
        )

    def _observed_asserting_hubs(self, namespace: str) -> tuple[str, ...]:
        """Return the hub ids observed asserting authority over ``namespace``, or empty."""
        if self._observed_asserting_hubs_feed is None:
            return ()
        return tuple(self._observed_asserting_hubs_feed(namespace))
