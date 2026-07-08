# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — end-to-end: the hub admits a socket only when it proves its identity

from __future__ import annotations

import json
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    sign_event_frame,
)

_SENDER = "proj/claude"


def _bound_hub(private_key: Ed25519PrivateKey, *, sender: str = _SENDER) -> SynapseHub:
    key = EventSignatureKey.from_private_key(
        key_id="k", private_key=private_key, senders=frozenset({sender})
    )
    bundle = EventSignatureTrustBundle(
        keys={"k": key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=64),
    )
    return SynapseHub(hub_id="idb", identity_trust_bundle=bundle, require_identity_binding=True)


def _signed_first_frame(private_key: Ed25519PrivateKey, *, sender: str = _SENDER) -> str:
    frame = {"sender": sender, "type": "heartbeat", "target": "System", "payload": "online"}
    signed = sign_event_frame(
        frame,
        key_id="k",
        private_key=private_key,
        nonce="reg-1",
        sequence=1,
        signed_at=time.time(),
    )
    return json.dumps(signed)


async def test_signed_registration_is_admitted_and_binds_end_to_end() -> None:
    private_key = Ed25519PrivateKey.generate()
    async with running_hub(_bound_hub(private_key)) as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(_signed_first_frame(private_key))
            await ws.send(json.dumps({"sender": _SENDER, "type": "who_request"}))
            who = await read_until_type(ws, "who_snapshot")

            assert _SENDER in who["online_agents"]


async def test_unsigned_registration_is_refused_end_to_end() -> None:
    private_key = Ed25519PrivateKey.generate()
    async with running_hub(_bound_hub(private_key)) as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(json.dumps({"sender": _SENDER, "type": "heartbeat", "target": "System"}))
            error = await read_until_type(ws, "error")

            assert error["verification_result"] == "missing_signature"


async def test_binding_off_admits_unsigned_registration_end_to_end() -> None:
    # Default posture: no bundle, binding off — an unsigned registration binds as before.
    async with running_hub(SynapseHub(hub_id="open")) as (_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, "welcome")
            await ws.send(json.dumps({"sender": _SENDER, "type": "heartbeat", "target": "System"}))
            await ws.send(json.dumps({"sender": _SENDER, "type": "who_request"}))
            who = await read_until_type(ws, "who_snapshot")

            assert _SENDER in who["online_agents"]
