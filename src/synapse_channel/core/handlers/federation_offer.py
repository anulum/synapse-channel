# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serving half of the federation-bundle exchange
"""Serving half of the federation-bundle exchange.

A peer domain's operator asks this hub for its federation-bundle material with a
:data:`~synapse_channel.core.protocol.MessageType.FEDERATION_OFFER_REQUEST` frame and is
answered with a single private
:data:`~synapse_channel.core.protocol.MessageType.FEDERATION_OFFER` carrying the bundle
mapping, framed by the shared codec (:mod:`synapse_channel.core.federation_wire`). The
offer is opt-in: a hub configures it with ``--federation-offer FILE`` pointing at its own
operator-authored bundle material, and a hub without one answers with an error frame.
Because the exchange rides the ordinary websocket surface, a hub started with ``--token``
gates the request like any other first frame.

Serving the offer moves only the *transport* of the peering ceremony onto the wire. The
trust decision stays out-of-band: the fetching operator compares bundle fingerprints with
this hub's operator over an independent channel and only then imports explicitly
(`docs/federated-trust-model.md`) — so this handler discloses material the offering
operator has deliberately published for peering, and nothing else. The file is re-read
per request, so rotated material serves without a hub restart; an unreadable or malformed
file is answered with a generic error frame while the detail goes to the operator log,
fail-visible on the side that can fix it.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from synapse_channel.core.federation import FederationPeer
from synapse_channel.core.federation_wire import (
    FederationWireError,
    decode_federation_offer,
    encode_federation_offer,
)
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from pathlib import Path

    from synapse_channel.core.hub import SynapseHub

logger = logging.getLogger(__name__)


async def handle_federation_offer_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer a peer operator's request for this hub's federation-bundle material.

    Parameters
    ----------
    hub : SynapseHub
        The hub whose ``federation_offer_path`` names the offered bundle file, or ``None``
        when no offer is configured.
    sender : str
        The requesting peer; the offer is addressed privately to it.
    data : dict[str, Any]
        The request frame; it carries no body fields beyond the envelope.
    websocket : Any
        The requesting socket the offer (or the error frame) is sent back on.
    """
    del data
    path = hub.federation_offer_path
    if path is None:
        await _send_error(hub, sender, websocket, "No federation offer is configured on this hub.")
        return
    try:
        peer = _read_offer(path)
    except (OSError, json.JSONDecodeError, FederationWireError) as exc:
        logger.warning("Federation offer at %s cannot be served: %s", path, exc)
        message = "The federation offer on this hub is unavailable."
        await _send_error(hub, sender, websocket, message)
        return
    await hub._send_json(
        websocket,
        hub._system(
            "Federation-bundle offer",
            msg_type=MessageType.FEDERATION_OFFER,
            target=sender,
            **encode_federation_offer(peer),
        ),
    )


def _read_offer(path: Path) -> FederationPeer:
    """Load and validate the offered bundle material from the configured file.

    Reading per request keeps the served material current with the file, so rotated keys
    or pins are republished the moment the file is written.

    Raises
    ------
    OSError
        If the file cannot be read.
    json.JSONDecodeError
        If the file is not JSON.
    FederationWireError
        If the JSON is not a well-formed bundle.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return decode_federation_offer(raw)


async def _send_error(hub: SynapseHub, sender: str, websocket: Any, message: str) -> None:
    """Send one private error frame back to the requesting socket."""
    await hub._send_json(
        websocket,
        hub._system(message, msg_type=MessageType.ERROR, target=sender),
    )
