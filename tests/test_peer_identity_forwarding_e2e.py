# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a hub's own forwards carry its peer identity signature end to end
"""A real hub's claim forward, operator relay and dead-letter forward are signed on the wire.

Each test runs a real origin hub with its real transports and a route whose
peer carries a :class:`PeerRegistrationSigner`. The owning peer is a real
websocket server that records the first frame it receives, and every recorded
frame must verify against a trust bundle naming the origin hub.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_binding import load_identity_trust_bundle, verify_registration
from synapse_channel.core.identity_keys import (
    generate_signing_key,
    public_key_b64,
    write_signing_key,
)
from synapse_channel.core.message_auth import SignedEventVerificationResult
from synapse_channel.core.multihub_claim_transport import ClaimForwardPeer
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import OperatorRelayPeer
from synapse_channel.core.operator_relay_wire import RelayActionRequest, encode_relay_request
from synapse_channel.core.peer_identity import PeerRegistrationSigner, load_peer_registration_signer
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

EDGE = "syn-edge"
OWNER = "syn-owner"
NAMESPACE = "OWNED"


def _material(tmp_path: Path) -> tuple[PeerRegistrationSigner, Any]:
    key = generate_signing_key()
    write_signing_key(tmp_path / "edge.pem", key)
    trust = tmp_path / "trust.json"
    trust.write_text(
        json.dumps(
            {"keys": [{"key_id": "edge", "public_key": public_key_b64(key), "senders": [EDGE]}]}
        ),
        encoding="utf-8",
    )
    return load_peer_registration_signer(tmp_path / "edge.pem", "edge"), load_identity_trust_bundle(
        trust
    )


@contextlib.asynccontextmanager
async def _owner() -> AsyncIterator[tuple[str, list[dict[str, Any]]]]:
    """A real websocket server standing in for the owning hub: record, then hang up."""
    frames: list[dict[str, Any]] = []

    async def handler(connection: ServerConnection) -> None:
        frames.append(json.loads(await connection.recv()))
        await connection.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield f"ws://127.0.0.1:{port}", frames


def _ownership() -> NamespaceOwnership:
    return NamespaceOwnership(owners={NAMESPACE: OWNER}, local_hub_id=EDGE)


def _valid(frame: dict[str, Any], trust: Any) -> bool:
    return (
        verify_registration(frame, trust_bundle=trust, now=time.time(), required_sender=EDGE)
        is SignedEventVerificationResult.VALID
    )


async def test_a_forwarded_claim_is_signed(tmp_path: Path) -> None:
    signer, trust = _material(tmp_path)
    async with _owner() as (owner_uri, frames):
        hub = SynapseHub(
            hub_id=EDGE,
            namespace_ownership=_ownership(),
            claim_peers={OWNER: ClaimForwardPeer(uri=owner_uri, signer=signer)},
        )
        async with running_hub(hub) as (_hub_ref, uri), connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await send_json(ws, sender=f"{NAMESPACE}/alice", type="heartbeat")
            await send_json(ws, sender=f"{NAMESPACE}/alice", type=MessageType.CLAIM, task_id="t1")
            await read_until_type(ws, MessageType.CLAIM_DENIED)
    [frame] = frames
    assert frame["type"] == MessageType.MULTIHUB_CLAIM_REQUEST
    assert _valid(frame, trust)


async def test_a_relayed_operator_action_is_signed(tmp_path: Path) -> None:
    signer, trust = _material(tmp_path)
    request = RelayActionRequest(
        action="release",
        namespace=NAMESPACE,
        task_id="t1",
        operator="ops-admin",
        origin_hub_id="asserted",
    )
    async with _owner() as (owner_uri, frames):
        hub = SynapseHub(
            hub_id=EDGE,
            namespace_ownership=_ownership(),
            relay_peers={OWNER: OperatorRelayPeer(uri=owner_uri, signer=signer)},
        )
        async with running_hub(hub) as (_hub_ref, uri), connect(uri) as ws:
            await read_until_type(ws, "welcome")
            agent = f"{NAMESPACE}/ops"
            await send_json(ws, sender=agent, type="heartbeat")
            await send_json(
                ws,
                sender=agent,
                type=MessageType.OPERATOR_RELAY_REQUEST,
                **encode_relay_request(request),
            )
            await read_until_type(ws, MessageType.OPERATOR_RELAY_RESULT)
    [frame] = frames
    assert frame["type"] == MessageType.OPERATOR_RELAY_REQUEST
    assert _valid(frame, trust)


async def test_a_forwarded_dead_letter_is_signed(tmp_path: Path) -> None:
    signer, trust = _material(tmp_path)
    store = EventStore(tmp_path / "events.db")
    async with _owner() as (owner_uri, frames):
        hub = SynapseHub(
            hub_id=EDGE,
            journal=store,
            dead_letter_escalation_threshold=1,
            namespace_ownership=_ownership(),
            relay_peers={OWNER: OperatorRelayPeer(uri=owner_uri, signer=signer)},
        )
        async with running_hub(hub) as (_hub_ref, uri), connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(
                json.dumps(
                    {
                        "sender": "ALPHA",
                        "type": "chat",
                        "target": f"{NAMESPACE}/reader",
                        "payload": "body",
                        "receipt_requested": True,
                    }
                )
            )
            await read_until_type(ws, "delivery_receipt")
    store.close()
    [frame] = frames
    assert frame["type"] == MessageType.DEAD_LETTER_FORWARDING
    assert _valid(frame, trust)
