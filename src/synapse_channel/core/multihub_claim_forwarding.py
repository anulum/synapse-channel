# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — origin half of cross-hub claim forwarding (forward and relay verdict)
"""Origin half of cross-hub claim forwarding: forward a remote claim and relay the verdict.

The serving half (:mod:`synapse_channel.core.handlers.multihub_claim`) applies a
forwarded claim on the hub that owns the target namespace, and the transport
(:mod:`synapse_channel.core.multihub_claim_transport`) carries the request across
the wire. :class:`ClaimForwarding` owns the origin-side step between ownership
authorisation (:class:`~synapse_channel.core.hub_frame_gates.HubFrameGates`) and
the transport: looking up the owner's route, building the
:class:`~synapse_channel.core.multihub_claim_wire.ClaimForwardRequest`, forwarding
with the peer's transport options, counting attempts and outcomes, refusing in
place on a timeout, and relaying the owner's grant or denial back to the claimant.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.core.hub_counters import HubCounters
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwarder,
    ClaimForwardError,
    ClaimForwardPeer,
    ClaimForwardTimeoutError,
)
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest, ClaimForwardResult
from synapse_channel.core.protocol import MessageType

logger = logging.getLogger("synapse.hub")


class ClaimForwarding:
    """Forward a remote-owned claim to its owning hub and relay the verdict to the claimant.

    Policy and transport inputs are captured at construction — the hub never
    mutates them after ``__init__`` — and the hub's per-socket send and
    system-message factory enter as injected callbacks, matching
    :class:`~synapse_channel.core.operator_relay_forwarding.OperatorRelayForwarding`.

    Parameters
    ----------
    claim_peers : dict[str, ClaimForwardPeer] or None
        How to reach each owning hub to forward a claim it owns, keyed by owning hub
        id; ``None`` forwards nothing and leaves the caller to refuse fail-closed.
    claim_forwarder : ClaimForwarder
        The seam that forwards a claim to an owning hub over the network.
    counters : HubCounters
        Shared live hub counters, incremented for forwarded-claim attempts and outcomes.
    hub_id : str
        This hub's stable id, stamped as the forwarding hub's local id on a forwarded
        claim.
    send_json : Callable[[Any, dict], Awaitable[None]]
        The hub's per-socket send (``hub.send_json``), used to relay the verdict or
        timeout refusal to the claimant.
    system : Callable[..., dict]
        The hub's system-message factory (``hub.system``), used to stamp each reply
        with the hub id.
    """

    def __init__(
        self,
        *,
        claim_peers: dict[str, ClaimForwardPeer] | None,
        claim_forwarder: ClaimForwarder,
        counters: HubCounters,
        hub_id: str,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
    ) -> None:
        self._claim_peers = claim_peers
        self._claim_forwarder = claim_forwarder
        self._counters = counters
        self._hub_id = hub_id
        self._send_json = send_json
        self._system = system

    async def forward(
        self,
        sender: str,
        namespace: str,
        task_id: str,
        data: dict[str, Any],
        owner_hub_id: str,
        websocket: Any,
    ) -> bool:
        """Forward a remote-owned claim to its owning hub and relay the verdict to the claimant.

        The owning hub applies the claim authoritatively and answers with a grant or a denial,
        which is relayed privately to the claimant — a grant carries the authentic lease fields,
        so the client sees the same ``CLAIM_GRANTED`` it would for a local claim.

        Returns
        -------
        bool
            ``True`` when the claim was forwarded and a verdict relayed, so the local grant path
            must not also run. ``False`` when no route is configured for the owner, the task
            carries no id to forward, or the forward failed — leaving the caller to refuse the
            claim and name the owner, fail-closed.
        """
        peer = self._claim_peers.get(owner_hub_id) if self._claim_peers else None
        if peer is None or not task_id:
            return False
        request = ClaimForwardRequest(
            namespace=namespace, claimant=sender, task_id=task_id, claim=data
        )
        self._counters.forwarded_claims += 1
        try:
            forward_kwargs: dict[str, Any] = {
                "uri": peer.uri,
                "local_id": self._hub_id,
                "token": peer.token,
            }
            if peer.connector is not None:
                forward_kwargs["connector"] = peer.connector
            if peer.signer is not None:
                forward_kwargs["signer"] = peer.signer
            result = await self._claim_forwarder(request, **forward_kwargs)
        except ClaimForwardTimeoutError:
            self._counters.forwarded_claim_timeouts += 1
            logger.warning(
                "Forwarding claim %r for %s to owner %s timed out",
                task_id,
                sender,
                owner_hub_id,
            )
            await self._send_json(
                websocket,
                self._system(
                    f"claim refused: owning hub {owner_hub_id!r} did not answer "
                    f"forwarded claim {task_id!r} before timeout",
                    msg_type=MessageType.CLAIM_DENIED,
                    target=sender,
                    task_id=task_id,
                    namespace=namespace,
                    owner_hub_id=owner_hub_id,
                    ownership="remote",
                    forward_error="timeout",
                ),
            )
            return True
        except ClaimForwardError:
            logger.warning(
                "Forwarding claim %r for %s to owner %s failed", task_id, sender, owner_hub_id
            )
            return False
        await self._relay_verdict(
            result,
            sender=sender,
            task_id=task_id,
            namespace=namespace,
            owner_hub_id=owner_hub_id,
            websocket=websocket,
        )
        return True

    async def _relay_verdict(
        self,
        result: ClaimForwardResult,
        *,
        sender: str,
        task_id: str,
        namespace: str,
        owner_hub_id: str,
        websocket: Any,
    ) -> None:
        """Relay the owning hub's grant or denial to the claimant and count the outcome."""
        if result.granted and result.grant is not None:
            self._counters.forwarded_claims_granted += 1
            await self._send_json(
                websocket,
                self._system(
                    result.detail or f"claim granted by {owner_hub_id}",
                    msg_type=MessageType.CLAIM_GRANTED,
                    target=sender,
                    **result.grant,
                ),
            )
        else:
            self._counters.forwarded_claims_denied += 1
            await self._send_json(
                websocket,
                self._system(
                    result.detail or "claim refused by the owning hub",
                    msg_type=MessageType.CLAIM_DENIED,
                    target=sender,
                    task_id=task_id,
                    namespace=namespace,
                    owner_hub_id=owner_hub_id,
                ),
            )
