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

from synapse_channel.core.acl import BOARD
from synapse_channel.core.atomic_operations import AtomicExecution, OperationDraft
from synapse_channel.core.identity_namespace import project_of
from synapse_channel.core.journal import EventKind, record_resource
from synapse_channel.core.protocol import RESOURCE_TYPE_ALIASES, MessageType
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_models import ResourceOffer
from synapse_channel.core.verb_access import field_access
from synapse_channel.core.verb_registry import VerbSpec
from synapse_channel.core.waiter_identity import waiter_owner

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from synapse_channel.core.capability import CapabilityRegistry
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.persistence import EventStore

    class OfferingsContext(HandlerContext, Protocol):
        """Capabilities consumed by offerings handlers and their callees."""

        @property
        def capabilities(self) -> CapabilityRegistry:
            """Return the capabilities used by this handler family."""
            ...


_SEAT_REQUIRED = (
    "Persistent capability registration requires a project-scoped seat identity (<project>/<seat>)."
)
_FOREIGN_CARD = (
    "Capability registration for another identity requires the "
    "connection to be that identity or a recognised receiver sidecar."
)


def _string_list(value: Any) -> list[str]:
    """Return the text of a list, or an empty list for any other shape."""
    return [str(item) for item in value] if isinstance(value, list) else []


def _project_seat(identity: str) -> bool:
    """Return whether the identity is a project-scoped seat with a real name."""
    seat = identity.split("/", 1)[1] if "/" in identity else ""
    return bool(project_of(identity) and seat.strip())


async def _reject_capability(hub: OfferingsContext, sender: str, websocket: Any, text: str) -> None:
    """Send one private error for a capability advertisement."""
    await hub.send_json(
        websocket,
        hub.system(text, msg_type=MessageType.ERROR, target=sender),
    )


async def _refuse_bad_flag(
    hub: OfferingsContext, sender: str, websocket: Any, name: str, value: Any
) -> bool:
    """Reject a present flag that is not a boolean and report whether it was refused."""
    if value is not None and not isinstance(value, bool):
        await _reject_capability(
            hub, sender, websocket, f"Malformed frame: '{name}' must be a boolean."
        )
        return True
    return False


async def _refuse_unscoped_persist(
    hub: OfferingsContext, sender: str, websocket: Any, identity: str, persist: Any
) -> bool:
    """Reject persistent registration that is not a project-scoped seat."""
    if persist and not _project_seat(identity):
        await _reject_capability(hub, sender, websocket, _SEAT_REQUIRED)
        return True
    return False


def _accepted_agent(sender: str, declared: Any) -> str | None:
    """Return the card identity, or none when the declaration impersonates someone."""
    if declared is None:
        return sender
    requested = str(declared).strip()
    # Mirror the mailbox structural gate: a connection may register a card
    # for itself or for the identity whose recognised receiver sidecar it
    # is (a wake listener registers its seat, not its receive-only name). Anything
    # else is an impersonation attempt and fails closed.
    if not requested or (requested != sender and waiter_owner(sender) != requested):
        return None
    return requested


def _capability_kwargs(
    data: dict[str, Any],
    agent: str,
    skills: list[str],
    task_classes: list[str],
    meta: Any,
) -> dict[str, Any]:
    """Return the stored card fields, with tags and metadata in their safe shapes."""
    return {
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


def _store_capability(
    hub: OfferingsContext,
    agent: str,
    card_kwargs: dict[str, Any],
    *,
    persist: Any,
    dispatchable: Any,
) -> Any:
    """Store a persistent registration or a live session card."""
    if persist:
        return hub.capabilities.advertise_persistent(
            agent,
            dispatchable=True if dispatchable is None else dispatchable,
            **card_kwargs,
        )
    return hub.capabilities.advertise(agent, **card_kwargs)


async def _broadcast_capability(
    hub: OfferingsContext, agent: str, card: Any, *, persist: Any
) -> None:
    """Broadcast the stored card, adding the persistent flags when they were stored."""
    card_payload = card.as_dict()
    if persist:
        registration = hub.capabilities.get_persistent(agent)
        card_payload["persistent"] = True
        card_payload["dispatchable"] = (
            registration.dispatchable if registration is not None else True
        )
    await hub.broadcast(
        hub.system(
            f"Capability advertised by {agent}",
            msg_type=MessageType.CAPABILITY_ADVERTISED,
            agent=agent,
            card=card_payload,
        )
    )


async def handle_advertise(
    hub: OfferingsContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Store an agent's capability card and broadcast it to the channel.

    With the additive ``persist`` flag the card becomes a persistent dispatch
    registration (survives disconnect, 24h refresh TTL) instead of a live
    session card; ``dispatchable`` opts the registration in or out of
    automated dispatch. Persistent registration is reserved for
    project-scoped seat identities and fails closed otherwise.
    """
    skills = _string_list(data.get("skills"))
    task_classes = _string_list(data.get("task_classes"))
    meta = data.get("meta")
    persist = data.get("persist")
    dispatchable = data.get("dispatchable")
    if await _refuse_bad_flag(hub, sender, websocket, "persist", persist):
        return
    if await _refuse_bad_flag(hub, sender, websocket, "dispatchable", dispatchable):
        return
    if await _refuse_unscoped_persist(hub, sender, websocket, sender, persist):
        return
    agent = _accepted_agent(sender, data.get("agent"))
    if agent is None:
        await _reject_capability(hub, sender, websocket, _FOREIGN_CARD)
        return
    if await _refuse_unscoped_persist(hub, sender, websocket, agent, persist):
        return
    card = _store_capability(
        hub,
        agent,
        _capability_kwargs(data, agent, skills, task_classes, meta),
        persist=persist,
        dispatchable=dispatchable,
    )
    await _broadcast_capability(hub, agent, card, persist=persist)


def _resource_persister(
    journal: EventStore | None,
) -> Callable[[tuple[str | None, ResourceOffer | None]], None] | None:
    """Return the step that journals a granted offer, or ``None`` without a journal."""
    if journal is None:
        return None
    store = journal

    def persist(result: tuple[str | None, ResourceOffer | None]) -> None:
        offer = result[1]
        if offer is not None:
            record_resource(store, offer)

    return persist


def _resource_offer_body(
    execution: AtomicExecution | None, draft: OperationDraft | None
) -> dict[str, Any] | None:
    """Return the offer body from an execution, or from a prepared draft."""
    if execution is not None:
        return execution.response
    return draft.response  # type: ignore[union-attr] # None return is not narrowed here


def _resource_request(data: dict[str, Any]) -> tuple[str, str, Any, Any] | None:
    """Return kind, name, capacity and meta, or none when kind or name is blank."""
    kind = str(data.get("kind") or data.get("resource_kind") or "").strip()
    name = str(data.get("name") or data.get("resource_name") or "").strip()
    if not kind or not name:
        return None
    return kind, name, data.get("capacity", 1), data.get("meta") or {}


async def _reject_resource(hub: OfferingsContext, sender: str, websocket: Any, text: str) -> None:
    """Send one private rejection for a resource offer."""
    await hub.send_json(
        websocket,
        hub.system(text, msg_type=MessageType.ERROR, target=sender),
    )


def _store_resource_offer(
    state: SynapseState,
    *,
    sender: str,
    kind: str,
    name: str,
    capacity: Any,
    meta: Any,
) -> tuple[str | None, ResourceOffer | None]:
    """Register one offer and return its key and the stored record."""
    key = state.offer_resource(sender, kind=kind, name=name, capacity=capacity, meta=meta)
    return key, state.resources.get(key) if key is not None else None


def _draft_resource_offer(
    hub: OfferingsContext,
    result: tuple[str | None, ResourceOffer | None],
    *,
    sender: str,
    kind: str,
    name: str,
) -> OperationDraft | None:
    """Build the broadcast and journal draft for a stored offer."""
    key, offer = result
    if key is None or offer is None:
        return None
    offered = hub.system(
        f"Resource offered by {sender}",
        msg_type=MessageType.RESOURCE_OFFERED,
        agent=sender,
        kind=kind,
        name=name,
        key=key,
    )
    return OperationDraft(
        response=offered,
        events=(
            (
                EventKind.RESOURCE,
                {
                    "agent": offer.agent,
                    "kind": offer.kind,
                    "name": offer.name,
                    "capacity": offer.capacity,
                    "meta": offer.meta,
                    "offered_at": offer.offered_at,
                },
            ),
        ),
        intent={"family": "resource", "response_type": MessageType.RESOURCE_OFFERED},
    )


async def handle_resource(
    hub: OfferingsContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Register a resource offer and broadcast it, or reject bad input."""
    requested = _resource_request(data)
    if requested is None:
        await _reject_resource(hub, sender, websocket, "resource offer requires kind+name")
        return
    kind, name, capacity, meta = requested
    journal = hub.journal

    def mutate(state: SynapseState) -> tuple[str | None, ResourceOffer | None]:
        return _store_resource_offer(
            state, sender=sender, kind=kind, name=name, capacity=capacity, meta=meta
        )

    def prepare(result: tuple[str | None, ResourceOffer | None]) -> OperationDraft | None:
        return _draft_resource_offer(hub, result, sender=sender, kind=kind, name=name)

    execution = await hub.run_atomic_operation(data, mutate, prepare)
    if execution is not None and execution.outcome in {"replayed", "conflict"}:
        assert execution.response is not None
        await hub.send_json(websocket, execution.response)
        return
    key, offer = (
        execution.mutation
        if execution is not None
        else await hub.state_mutations.run(
            hub.state,
            mutate,
            persist=_resource_persister(journal),
        )
    )
    if key is None:
        await _reject_resource(hub, sender, websocket, "resource offer quota exceeded")
        return
    draft = None if execution is not None else prepare((key, offer))
    offered = _resource_offer_body(execution, draft)
    assert offered is not None
    if execution is None:
        hub.remember(data, offered)
    await hub.broadcast(offered)
    if execution is not None:
        await hub.settle_atomic_operation(data)


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.ADVERTISE,),
        handler=handle_advertise,
        reply_types=(MessageType.CAPABILITY_ADVERTISED,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=field_access(BOARD, "capability", "agent", fallback="*"),
        event_kinds=(),
        minimum_wire_version=1,
        commands=(),
    ),
    VerbSpec(
        request_types=tuple(sorted(RESOURCE_TYPE_ALIASES)),
        handler=handle_resource,
        reply_types=(MessageType.RESOURCE_OFFERED,),
        mutates=True,
        replay_protected=True,
        mutation_guarded=True,
        accesses=field_access(BOARD, "resource", "name", fallback="*"),
        event_kinds=(EventKind.RESOURCE,),
        minimum_wire_version=1,
        commands=(),
    ),
)
