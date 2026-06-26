# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub registration and input guards

from __future__ import annotations

import json
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from hub_e2e_helpers import read_json, read_until_type, running_hub
from synapse_channel.core.hub import MAX_LOG_PAYLOAD, SynapseHub
from synapse_channel.core.ratelimit import RateLimiter


async def test_welcome_arrives_over_real_websocket() -> None:
    async with running_hub(SynapseHub(hub_id="syn-e2e")) as (_, uri):
        async with connect(uri) as websocket:
            welcome = await read_json(websocket)
    assert welcome["type"] == "welcome"
    assert welcome["hub_id"] == "syn-e2e"


def test_redact_payload_truncates_a_long_payload() -> None:
    assert SynapseHub._redact_payload("short") == "short"
    long = "x" * 500
    redacted = SynapseHub._redact_payload(long)
    assert redacted.startswith("x" * MAX_LOG_PAYLOAD)
    assert f"(+{500 - MAX_LOG_PAYLOAD} chars)" in redacted


async def test_malformed_json_returns_error_end_to_end() -> None:
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send("{not json")
            error = await read_until_type(websocket, "error")
    assert "Malformed JSON" in error["payload"]


async def test_deeply_nested_json_is_rejected_not_crashed_end_to_end() -> None:
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send("[" * 500 + "]" * 500)
            error = await read_until_type(websocket, "error")
    assert "Malformed JSON" in error["payload"]


async def test_host_rate_limiter_refuses_a_flooding_host_end_to_end() -> None:
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=2.0))
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            for payload in ("1", "2", "3"):
                await websocket.send(
                    json.dumps({"sender": "A", "type": "chat", "payload": payload})
                )
            error = await read_until_type(websocket, "error")
    assert "Host rate limit exceeded" in error["payload"]


async def test_host_rate_limiter_meters_heartbeats_end_to_end() -> None:
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=1.0))
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps({"sender": "A", "type": "heartbeat"}))
            await websocket.send(json.dumps({"sender": "A", "type": "heartbeat"}))
            error = await read_until_type(websocket, "error")
    assert "Host rate limit exceeded" in error["payload"]


async def test_max_connections_per_host_refuses_excess_sockets_end_to_end() -> None:
    hub = SynapseHub(max_connections_per_host=2)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as first, connect(uri) as second:
            await read_until_type(first, "welcome")
            await read_until_type(second, "welcome")
            async with connect(uri) as third:
                try:
                    await third.recv()
                except ConnectionClosedError as exc:
                    assert exc.rcvd is not None
                    assert exc.rcvd.code == 4015
                    assert "too many connections from host" in exc.rcvd.reason
                else:  # pragma: no cover - the assertion above must be reached.
                    raise AssertionError("third same-host connection was not refused")
        async with connect(uri) as admitted_after_disconnect:
            assert (await read_until_type(admitted_after_disconnect, "welcome"))[
                "type"
            ] == "welcome"


def test_remote_host_handles_tuple_bare_and_missing() -> None:
    class _WS:
        def __init__(self, addr: Any) -> None:
            self.remote_address = addr

    assert SynapseHub._remote_host(_WS(("1.2.3.4", 9))) == "1.2.3.4"
    assert SynapseHub._remote_host(_WS("sock-path")) == "sock-path"
    assert SynapseHub._remote_host(_WS(None)) == "unknown"
    assert SynapseHub._remote_host(object()) == "unknown"


async def test_anonymous_sender_gets_generated_name_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps({"type": "heartbeat"}))
            await read_until_type(websocket, "presence_update")
            assert any(name.startswith("anon-") for name in hub.agent_sockets)
