# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real WebSocket frame admission regression tests
"""Exercise envelope admission through the serving hub."""

from __future__ import annotations

import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.ratelimit import RateLimiter


@pytest.mark.parametrize(
    ("invalid", "reason"),
    [(b"null", "expected a JSON object"), (b"{broken", "Malformed JSON")],
)
async def test_invalid_first_frame_keeps_the_next_connections_registration_quota(
    invalid: bytes, reason: str
) -> None:
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=1.0))
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as rejected:
            await read_until_type(rejected, "welcome")
            await rejected.send(invalid)
            error = await read_until_type(rejected, "error")
            assert reason in error["payload"]
            with pytest.raises(ConnectionClosedError) as closed:
                await rejected.recv()
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 4010
            assert hub.online_agents() == []
        async with connect(uri) as admitted:
            await read_until_type(admitted, "welcome")
            await admitted.send(json.dumps({"sender": "FRAME/seat", "type": "heartbeat"}))
            joined = await read_until_type(admitted, "presence_update")
            assert joined["agent"] == "FRAME/seat"


async def test_binary_registration_and_chat_pass_the_same_real_frame_pipeline() -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps({"sender": "FRAME/seat", "type": "heartbeat"}).encode("utf-8")
            )
            await read_until_type(websocket, "presence_update")
            await websocket.send(
                json.dumps(
                    {
                        "sender": "FRAME/seat",
                        "target": None,
                        "type": "chat",
                        "payload": "správa ✓",
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
            )
            echoed = await read_until_type(websocket, "chat")
    assert echoed["payload"] == "správa ✓" and echoed["sender"] == "FRAME/seat"
