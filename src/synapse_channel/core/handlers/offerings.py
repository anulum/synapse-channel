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

from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.journal import record_resource
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_models import ResourceOffer

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


async def handle_advertise(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Store an agent's capability card and broadcast it to the channel.

    With the additive ``persist`` flag the card becomes a persistent dispatch
    registration (survives disconnect, 24h refresh TTL) instead of a live
    session card; ``dispatchable`` opts the registration in or out of
    automated dispatch. Persistent registration is reserved for
    project-scoped seat identities and fails closed otherwise.
    """
    raw_skills = data.get("skills")
    raw_classes = data.get("task_classes")
    skills = [str(s) for s in raw_skills] if isinstance(raw_skills, list) else []
    task_classes = [str(c) for c in raw_classes] if isinstance(raw_classes, list) else []
    meta = data.get("meta")
    persist = data.get("persist")
    dispatchable = data.get("dispatchable")
    if persist is not None and not isinstance(persist, bool):
        await hub._send_json(
            websocket,
            hub._system(
                "Malformed frame: 'persist' must be a boolean.",
                msg_type=MessageType.ERROR,
                target=sender,
            ),
        )
        return
    if dispatchable is not None and not isinstance(dispatchable, bool):
        await hub._send_json(
            websocket,
            hub._system(
                "Malformed frame: 'dispatchable' must be a boolean.",
                msg_type=MessageType.ERROR,
                target=sender,
            ),
        )
        return
    if persist:
        seat = sender.split("/", 1)[1] if "/" in sender else ""
        if not project_of(sender) or not seat.strip():
            await hub._send_json(
                websocket,
                hub._system(
                    "Persistent capability registration requires a project-scoped "
                    "seat identity (<project>/<seat>).",
                    msg_type=MessageType.ERROR,
                    target=sender,
                ),
            )
            return
    agent = sender
    declared_agent = data.get("agent")
    if declared_agent is not None:
        requested = str(declared_agent).strip()
        # Mirror the mailbox structural gate: a connection may register a card
        # for itself or for the identity whose ``-rx`` sidecar it is (a wake
        # listener registers its seat, not its receive-only name). Anything
        # else is an impersonation attempt and fails closed.
        if not requested or (requested != sender and sender != f"{requested}-rx"):
            await hub._send_json(
                websocket,
                hub._system(
                    "Capability registration for another identity requires the "
                    "connection to be that identity or its -rx sidecar.",
                    msg_type=MessageType.ERROR,
                    target=sender,
                ),
            )
            return
        agent = requested
        if persist:
            agent_seat = agent.split("/", 1)[1] if "/" in agent else ""
            if not project_of(agent) or not agent_seat.strip():
                await hub._send_json(
                    websocket,
                    hub._system(
                        "Persistent capability registration requires a project-scoped "
                        "seat identity (<project>/<seat>).",
                        msg_type=MessageType.ERROR,
                        target=sender,
                    ),
                )
                return
    card_kwargs: dict[str, Any] = {
        "description": str(data.get("description") or ""),
        "skills": skills,
        "task_classes": task_classes,
        "model": str(data.get("model") or ""),
        "project": project_of(agent),
        "manifest_digest": str(data.get("manifest_digest") or ""),
        "contracts": data.get("contracts"),
        "meta": meta if isinstance(meta, dict) else None,
        "signature": data.get("signature"),
    }
    if persist:
        card = hub.capabilities.advertise_persistent(
            agent,
            dispatchable=True if dispatchable is None else dispatchable,
            **card_kwargs,
        )
    else:
        card = hub.capabilities.advertise(agent, **card_kwargs)
    card_payload = card.as_dict()
    if persist:
        registration = hub.capabilities.get_persistent(agent)
        card_payload["persistent"] = True
        card_payload["dispatchable"] = (
            registration.dispatchable if registration is not None else True
        )
    await hub._broadcast(
        hub._system(
            f"Capability advertised by {agent}",
            msg_type=MessageType.CAPABILITY_ADVERTISED,
            agent=agent,
            card=card_payload,
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
    journal = hub.journal

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

    def mutate(state: SynapseState) -> tuple[str | None, ResourceOffer | None]:
        key = state.offer_resource(
            sender,
            kind=kind,
            name=name,
            capacity=capacity,
            meta=meta,
        )
        return key, state.resources.get(key) if key is not None else None

    def persist(result: tuple[str | None, ResourceOffer | None]) -> None:
        if journal is None:
            raise RuntimeError("resource persistence requested without a journal")
        offer = result[1]
        if offer is not None:
            record_resource(journal, offer)

    key, _offer = await hub.state_mutations.run(
        hub.state,
        mutate,
        persist=persist if journal is not None else None,
    )
    if key is None:
        await hub._send_json(
            websocket,
            hub._system(
                "resource offer quota exceeded",
                msg_type=MessageType.ERROR,
                target=sender,
            ),
        )
        return
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
