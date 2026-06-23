# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — offering handlers (capability cards + resource offers)
"""Offering handlers — an agent announces what it can provide.

A capability card describes an agent's skills and task classes; a resource offer
registers a named, capacity-bounded resource. Both register the offering in the
hub's registry and broadcast it to the channel so peers can route work toward it;
a malformed resource offer is privately rejected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.core.journal import record_resource
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


async def handle_advertise(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Store an agent's capability card and broadcast it to the channel."""
    raw_skills = data.get("skills")
    raw_classes = data.get("task_classes")
    skills = [str(s) for s in raw_skills] if isinstance(raw_skills, list) else []
    task_classes = [str(c) for c in raw_classes] if isinstance(raw_classes, list) else []
    meta = data.get("meta")
    card = hub.capabilities.advertise(
        sender,
        description=str(data.get("description") or ""),
        skills=skills,
        task_classes=task_classes,
        model=str(data.get("model") or ""),
        meta=meta if isinstance(meta, dict) else None,
    )
    await hub._broadcast(
        hub._system(
            f"Capability advertised by {sender}",
            msg_type=MessageType.CAPABILITY_ADVERTISED,
            agent=sender,
            card=card.as_dict(),
        )
    )


async def handle_resource(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Register a resource offer and broadcast it, or reject bad input."""
    kind = str(data.get("kind") or data.get("resource_kind") or "").strip()
    name = str(data.get("name") or data.get("resource_name") or "").strip()
    capacity = data.get("capacity", 1)
    meta = data.get("meta") or {}

    if not kind or not name:
        await hub._send_json(
            websocket,
            hub._system(
                "resource offer requires kind+name",
                msg_type=MessageType.ERROR,
                target=sender,
            ),
        )
        return

    key = hub.state.offer_resource(sender, kind=kind, name=name, capacity=capacity, meta=meta)
    if hub.journal is not None:
        record_resource(hub.journal, hub.state.resources[key])
    offered = hub._system(
        f"Resource offered by {sender}",
        msg_type=MessageType.RESOURCE_OFFERED,
        agent=sender,
        kind=kind,
        name=name,
        key=key,
    )
    hub._remember(data, offered)
    await hub._broadcast(offered)
