# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bind delivery capability to a verified connection
"""Admit delivery capabilities only on an identity-bound registration frame."""

from __future__ import annotations

from typing import Any

from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.hub_clients import DeliverySession, HubClientRegistry
from synapse_channel.core.protocol import MessageType


def bind_delivery_registration(
    clients: HubClientRegistry,
    *,
    sender: str,
    websocket: Any,
    data: dict[str, Any],
    msg_type: str,
    was_bound: bool,
    durable: bool,
    stable_hub_id: str | None,
) -> DeliverySession | None:
    """Strip the process token and bind a live version-three capability record.

    The hub calls this only after authentication and name ownership have bound
    ``sender`` to ``websocket``. A v3 sender with no receiver capabilities needs
    no session token. A receiver requires a durable journal and an explicit
    stable hub id before it may advertise queue semantics.
    """
    token = data.pop("delivery_session_token", None)
    capabilities = data.pop("delivery_capabilities", None)
    if token is None and capabilities is None:
        return None
    if was_bound or msg_type != MessageType.HEARTBEAT:
        raise DeliveryRefusal("invalid_shape", "delivery session belongs on registration")
    if data.get("protocol_version") != 3 or isinstance(data.get("protocol_version"), bool):
        raise DeliveryRefusal("unsupported_protocol", "delivery session requires wire version 3")
    if not durable or not stable_hub_id:
        raise DeliveryRefusal("unsupported_profile", "delivery requires a durable stable hub")
    return clients.bind_delivery_session(
        sender,
        websocket,
        token=token,
        capabilities=capabilities,
        hub_id=stable_hub_id,
    )
