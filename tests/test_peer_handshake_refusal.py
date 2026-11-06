# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a peer that refuses the WebSocket handshake is a transport failure
"""A peer answering the upgrade with an HTTP error is an ordinary, typed transport failure.

A proxy in front of a peer hub that is down answers the upgrade with ``502``. Every peer
transport used to let that ``InvalidStatus`` escape: a forwarded chat or roster request ended
the requester's connection, and a pending forward stopped the retry loop. The peer here is a
real loopback HTTP server that refuses every upgrade.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.cli_doctor_federation import (
    FederationDoctorError,
    FederationDoctorPeer,
    probe_federation_peer,
)
from synapse_channel.core.dead_letter_forwarding import DeadLetterForwardError, forwarding_notice
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.federation_fetch import FederationFetchError, fetch_federation_offer
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_forward_origin import run_forward_retries
from synapse_channel.core.message_forward_transport import (
    MessageForwardPeer,
    MessageForwardTransportError,
    forward_message,
)
from synapse_channel.core.message_forward_wire import MessageForwardRequest
from synapse_channel.core.multihub_claim_transport import ClaimForwardError, forward_claim
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest
from synapse_channel.core.operator_relay_transport import RelayTransportError, relay_operator_action
from synapse_channel.core.operator_relay_wire import RelayActionRequest
from synapse_channel.core.spend_transport import SpendTransportError, request_spend


@asynccontextmanager
async def _refusing_peer() -> AsyncIterator[str]:
    """Serve loopback HTTP that answers every WebSocket upgrade with ``502 Bad Gateway``."""

    def refuse(connection: ServerConnection, _request: Request) -> Response:
        return connection.respond(502, "bad gateway\n")

    async def never(_socket: ServerConnection) -> None:
        raise AssertionError("no upgrade is ever accepted")

    async with serve(never, "127.0.0.1", 0, process_request=refuse) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


def _relay() -> RelayActionRequest:
    return RelayActionRequest(
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="T1",
        operator="ops",
        origin_hub_id="origin",
        reason="wedged",
        idem_key="relay-1",
    )


_CALLS: dict[str, tuple[type[Exception], Callable[[str], Awaitable[Any]]]] = {
    "message": (
        MessageForwardTransportError,
        lambda uri: forward_message(
            MessageForwardRequest(forward_id="a" * 32, kind="who", sender_seat="P/a"),
            peer=MessageForwardPeer(uri),
            local_id="origin",
        ),
    ),
    "claim": (
        ClaimForwardError,
        lambda uri: forward_claim(
            ClaimForwardRequest(namespace="P", claimant="P/a", task_id="t", claim={}),
            uri=uri,
            local_id="origin",
        ),
    ),
    "dead-letter": (
        DeadLetterForwardError,
        lambda uri: forward_dead_letter(
            forwarding_notice("OWNED/reader", 3, origin_hub_id="origin", owner_hub_id="owner"),
            uri=uri,
            local_id="origin",
        ),
    ),
    "federation-offer": (
        FederationFetchError,
        lambda uri: fetch_federation_offer(uri, local_id="origin"),
    ),
    "operator-relay": (
        RelayTransportError,
        lambda uri: relay_operator_action(_relay(), uri=uri, local_id="origin"),
    ),
    "spend": (
        SpendTransportError,
        lambda uri: request_spend("settle", {"pool_id": "p"}, uri=uri, local_id="origin"),
    ),
    "doctor-probe": (
        FederationDoctorError,
        lambda uri: probe_federation_peer(
            FederationDoctorPeer(peer_id="alpha", uri=uri), 0, "doctor", None
        ),
    ),
}


@pytest.mark.parametrize("transport", sorted(_CALLS))
async def test_every_peer_transport_types_a_refused_handshake(transport: str) -> None:
    """Each transport raises its own error, naming the status, instead of ``InvalidStatus``."""
    expected, call = _CALLS[transport]
    async with _refusing_peer() as uri:
        with pytest.raises(expected, match="HTTP 502"):
            await call(uri)


async def _send_from(uri: str, frame: dict[str, Any], reply: str) -> dict[str, Any]:
    async with connect(uri) as sender:
        await read_until_type(sender, "welcome")
        await sender.send(
            json.dumps({"sender": "PROJ/a", "type": "heartbeat", "protocol_version": 3})
        )
        await sender.send(json.dumps({"sender": "PROJ/a", **frame}))
        return await read_until_type(sender, reply)


async def test_a_refusing_peer_leaves_chat_pending_and_the_requester_connected() -> None:
    """Chat stays pending and retries; a roster request gets a named refusal; nothing drops."""
    async with _refusing_peer() as peer_uri:
        hub = SynapseHub(hub_id="origin", message_peers={"peer": MessageForwardPeer(peer_uri)})
        async with running_hub(hub) as (_, uri):
            receipt = await _send_from(
                uri,
                {
                    "type": "chat",
                    "target": "PROJ/b@peer",
                    "payload": "hi",
                    "receipt_requested": True,
                },
                "delivery_receipt",
            )
            roster = await _send_from(uri, {"type": "who_request", "hub": "peer"}, "error")
            assert receipt["forward_state"] == "pending"
            assert "peer_unreachable" in roster["payload"]
            entry = hub.message_forward_ledger.outbox_entry(receipt["forward_id"])
            assert entry is not None and entry.attempts == 1
            await run_forward_retries(hub, now=time.time() + 3600)
            retried = hub.message_forward_ledger.outbox_entry(receipt["forward_id"])
            assert retried is not None
            assert (retried.state, retried.attempts) == ("pending", 2)
