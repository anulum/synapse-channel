# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serving half of cross-hub dead-letter forwarding (record on the owning hub)
"""Serving half of cross-hub dead-letter forwarding: record a peer's pointer on the owning hub.

The initiating half (:mod:`synapse_channel.core.dead_letter_forwarding_transport`) hands a blackhole
pointer to the hub whose domain owns the blackholed target; this module receives it. A peer forwards
a :data:`~synapse_channel.core.protocol.MessageType.DEAD_LETTER_FORWARDING` frame carrying the
:func:`~synapse_channel.core.dead_letter_forwarding.forwarding_notice` pointer, and this hub — which
owns the target's namespace and can actually reach the reader — journals the fact and tells its
operators, so a gap invisible on the owning side becomes visible where it can be acted on.

Receiving a pointer causes a durable audit and a local broadcast, so it is a trust boundary and the
gate fails closed, exactly like the operator relay's serving half:

* the peer must be authorised by the hub's
  :class:`~synapse_channel.core.multihub_serving.MultiHubServingPolicy` — a hub with no policy
  accepts no forwarding at all, since acting on an unauthenticated peer's signal is never safe;
* this hub must authoritatively own the target's namespace — a pointer for a namespace this hub does
  not own is misrouted or spoofed and is dropped;
* a malformed frame is dropped.

Forwarding is **fire-and-forget**, so a refusal is silent (logged, never answered): the origin
awaits no reply, and the pointer is advisory, not a request for a verdict. Unlike the operator relay
it mutates no coordination state — it records an audit-only event and notifies operators — so there
is nothing to release and no result to return, only a gap made visible on the side that owns it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.dead_letter_forwarding import (
    DeadLetterForwardingWireError,
    ForwardingNotice,
    decode_forwarding_notice,
    incoming_forwarding_notice,
)
from synapse_channel.core.journal import DEAD_LETTER_DIRECTION_IN, record_dead_letter_forwarding
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

logger = logging.getLogger("synapse.messaging")


async def handle_dead_letter_forwarding(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Record a peer's dead-letter pointer and tell this hub's operators, or drop it fail-closed.

    Parameters
    ----------
    hub : SynapseHub
        The owning hub the pointer names; it journals the incoming forwarding and broadcasts it.
    sender : str
        The forwarding peer hub; the serving policy authorises the pointer against this
        cryptographically verified identity, which is also recorded as the audit's ``peer``.
    data : dict[str, Any]
        The forwarding frame; its nested pointer is decoded and validated before anything acts.
    websocket : Any
        The peer socket, read by the serving policy to authorise the peer. No reply is sent on it —
        forwarding is fire-and-forget.
    """
    try:
        notice = decode_forwarding_notice(data)
    except DeadLetterForwardingWireError:
        logger.warning("Dropped malformed dead-letter forwarding from peer %r", sender)
        return
    if not _authorised(hub, sender, notice, websocket):
        return
    # Persist before notifying, so a reader that sees the broadcast can trust the audit is written.
    if hub.journal is not None:
        record_dead_letter_forwarding(
            hub.journal,
            {
                "target": notice.target,
                "count": notice.count,
                "origin_hub_id": notice.origin_hub_id,
                "owner_hub_id": notice.owner_hub_id,
                "direction": DEAD_LETTER_DIRECTION_IN,
                "peer": sender,
            },
        )
    await hub._broadcast(
        hub._system(
            incoming_forwarding_notice(notice.target, notice.count, notice.origin_hub_id),
            msg_type=MessageType.DEAD_LETTER_FORWARDING,
            forwarding_target=notice.target,
            forwarding_count=notice.count,
            origin_hub_id=notice.origin_hub_id,
        )
    )


def _authorised(hub: SynapseHub, sender: str, notice: ForwardingNotice, websocket: Any) -> bool:
    """Return whether ``sender`` may forward ``notice`` to this hub, logging any refusal.

    Two deny-closed gates must both pass: the peer is authorised by the hub's serving policy (a hub
    with no policy authorises nothing), and this hub authoritatively owns the target's namespace (a
    pointer for a namespace this hub does not own is misrouted or spoofed). Neither gate needs the
    pointer's self-asserted ``owner_hub_id`` — ownership is decided by this hub's own map, not by a
    field the sender controls.
    """
    policy = hub.multihub_serving_policy
    if policy is None or not policy.authorise(sender=sender, websocket=websocket).allowed:
        logger.warning("Refused dead-letter forwarding from unauthorised peer %r", sender)
        return False
    ownership = hub.namespace_ownership
    namespace = project_of(notice.target)
    if ownership is None or not ownership.resolve(namespace).grants_locally:
        logger.warning(
            "Refused dead-letter forwarding for %r from peer %r: namespace %r is not owned here",
            notice.target,
            sender,
            namespace,
        )
        return False
    return True
