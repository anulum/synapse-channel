# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — delivery-session registration effects
"""Bind a delivery session and publish its existing registration response."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.delivery_registration import bind_delivery_registration
from synapse_channel.core.hub_clients import DeliverySession, HubClientRegistry
from synapse_channel.core.protocol import MessageType


@dataclass(frozen=True, kw_only=True)
class SenderRegistrationPorts:
    """Retain the owning hub's authentication and name-binding gates.

    Parameters
    ----------
    authorise, verify_identity : callable
        Existing credential and signed-registration admission gates.
    peer_proven, record_identity_proof : callable
        Existing peer certificate predicate and verified identity recorder.
    resolve_sender : callable
        Bind the sender with takeover, lease request and owner lease inputs.
    send_welcome : callable
        Release the secured hub's withheld welcome after successful binding.
    """

    authorise: Callable[[str, dict[str, Any], Any], Awaitable[bool]]
    verify_identity: Callable[[str, dict[str, Any], Any], Awaitable[bool]]
    peer_proven: Callable[[str, Any], bool]
    record_identity_proof: Callable[[str, dict[str, Any], Any], None]
    resolve_sender: Callable[[str, Any, bool, bool, str], Awaitable[str | None]]
    send_welcome: Callable[[Any], Awaitable[None]]


async def register_sender(
    clients: HubClientRegistry,
    ports: SenderRegistrationPorts,
    sender: str,
    data: dict[str, Any],
    websocket: Any,
    *,
    secured: Callable[[], bool],
) -> tuple[str, bool] | None:
    """Verify registration before consuming credentials and binding its name.

    Parameters
    ----------
    clients, ports : HubClientRegistry, SenderRegistrationPorts
        The live socket registry and deferred admission effects.
    sender, data, websocket : str, dict, object
        Claimed sender and admitted envelope on its original socket.
    secured : callable
        Read the current secured posture after successful name binding.

    Returns
    -------
    tuple or None
        Resolved sender and original bound predicate, or None after refusal.
        Reserved names are refused before authentication or identity pinning.
        The registration signature is verified before its token is removed.
    """
    if clients.is_reserved_sender(sender):
        await ports.resolve_sender(sender, websocket, False, False, "")
        return None
    was_bound = clients.is_bound(websocket)
    if not await ports.authorise(sender, data, websocket):
        return None
    if not was_bound and not ports.peer_proven(sender, websocket):
        if not await ports.verify_identity(sender, data, websocket):
            return None
        ports.record_identity_proof(sender, data, websocket)
    data.pop("token", None)
    resolved = await ports.resolve_sender(
        sender,
        websocket,
        bool(data.get("takeover")),
        bool(data.get("lease")),
        str(data.get("owner_lease") or ""),
    )
    if resolved is None:
        return None
    if secured() and not was_bound:
        await ports.send_welcome(websocket)
    return resolved, was_bound


@dataclass(frozen=True, kw_only=True)
class DeliveryRegistrationPorts:
    """The owning hub's deferred delivery-registration effects.

    Parameters
    ----------
    send_json, system : callable
        Existing socket transport and system-message factory.
    close_socket : callable
        Close one refused socket with its code and reason.
    supersede : callable
        Supersede older sessions of the resolved sender by incarnation.
    """

    send_json: Callable[[Any, dict[str, Any]], Awaitable[None]]
    system: Callable[..., dict[str, Any]]
    close_socket: Callable[[Any, int, str], Awaitable[None]]
    supersede: Callable[[str], Awaitable[None]]


async def _refuse(
    ports: DeliveryRegistrationPorts, sender: str, websocket: Any, refusal: DeliveryRefusal
) -> None:
    """Send the existing deliberate refusal before closing its socket."""
    await ports.send_json(
        websocket,
        ports.system(
            str(refusal), msg_type=MessageType.ERROR, target=sender, reason_code=refusal.code
        ),
    )
    await ports.close_socket(websocket, 4020, "delivery registration refused")


async def _publish(
    ports: DeliveryRegistrationPorts, sender: str, websocket: Any, session: DeliverySession
) -> None:
    """Supersede older receiver incarnations before advertising the new one."""
    await ports.supersede(session.incarnation)
    await ports.send_json(
        websocket,
        ports.system(
            "Delivery session registered.",
            msg_type=MessageType.DELIVERY_SESSION,
            target=sender,
            incarnation=session.incarnation,
            capabilities=session.capabilities,
            protocol_version=3,
        ),
    )


async def register_delivery_session(
    clients: HubClientRegistry,
    ports: DeliveryRegistrationPorts,
    *,
    sender: str,
    websocket: Any,
    data: dict[str, Any],
    msg_type: str,
    was_bound: bool,
    durable: bool,
    stable_hub_id: str | None,
) -> bool:
    """Apply delivery registration after identity and name ownership admission.

    Parameters
    ----------
    clients : HubClientRegistry
        Existing registry whose socket is already bound to the sender.
    ports : DeliveryRegistrationPorts
        Effects retained by the owning hub.
    sender, websocket, data, msg_type : str, object, dict, str
        Resolved sender and original registration frame.
    was_bound, durable : bool
        Original connection and durable-journal predicates.
    stable_hub_id : str or None
        Operator-selected hub identity, unchanged from ingress.

    Returns
    -------
    bool
        False after refusing and closing; otherwise registration may continue.
    """
    try:
        session = bind_delivery_registration(
            clients,
            sender=sender,
            websocket=websocket,
            data=data,
            msg_type=msg_type,
            was_bound=was_bound,
            durable=durable,
            stable_hub_id=stable_hub_id,
        )
    except DeliveryRefusal as refusal:
        await _refuse(ports, sender, websocket, refusal)
        return False
    if session is not None:
        await _publish(ports, sender, websocket, session)
    return True
