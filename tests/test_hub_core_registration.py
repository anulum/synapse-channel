# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub registration and input guards

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
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


async def test_non_object_json_frame_is_refused_not_crashed_end_to_end() -> None:
    # A valid-JSON frame need not be an object: a list, null, or a bare number all
    # decode cleanly, then data.get("sender") would raise AttributeError and drop
    # the socket with a 1011. It must get a clean protocol error, and the
    # connection must survive to serve the next frame.
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(["not", "a", "dict"]))
            error = await read_until_type(websocket, "error")
            # The connection is still alive — a second non-object frame is refused
            # the same way rather than the socket being gone.
            await websocket.send(json.dumps(42))
            second = await read_until_type(websocket, "error")
    assert "expected a JSON object" in error["payload"]
    assert "expected a JSON object" in second["payload"]


async def test_mistyped_routing_field_is_refused_not_coerced_end_to_end() -> None:
    # A routing/identity field present as a non-string would be str()-coerced into a
    # plausible identity or route (sender ["spoof","victim"] -> "['spoof', 'victim']").
    # It must get a clean protocol error that names the field, and — as with the
    # non-object guard beside it — the connection must survive to serve the next frame.
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps({"sender": ["spoof", "victim"], "type": "chat"}))
            error = await read_until_type(websocket, "error")
            # The socket is still alive — a mistyped type on the next frame is refused
            # the same way rather than the connection being gone.
            await websocket.send(json.dumps({"sender": "agent-a", "type": True}))
            second = await read_until_type(websocket, "error")
    assert "'sender' must be a string" in error["payload"]
    assert "'type' must be a string" in second["payload"]


async def test_a_mistyped_frame_is_still_charged_to_the_host_rate_limiter_end_to_end() -> None:
    # Regression (SOL audit of F9): the mistyped-routing-field guard runs AFTER the per-host
    # charge, so a flood of malformed object frames is still metered rather than cheaply
    # bypassing the limiter while still forcing hub work. With burst=1, the first (mistyped)
    # frame consumes the single host token and is refused for its bad field; the second
    # same-host frame is then refused by the host limiter before any further work — proving
    # the malformed frame was charged, not waved through.
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=1.0))
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps({"sender": ["flood"], "type": "chat"}))
            first = await read_until_type(websocket, "error")
            await websocket.send(json.dumps({"sender": "A", "type": "chat", "payload": "x"}))
            second = await read_until_type(websocket, "error")
    assert "'sender' must be a string" in first["payload"]
    assert "Host rate limit exceeded" in second["payload"]


async def test_untrusted_log_fields_are_rendered_without_control_characters(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A client controls its own sender/type/payload. A crafted newline must not
    # forge a second hub log line, and a CR/ANSI must not rewrite the operator's
    # terminal: the hub renders every untrusted field one-line, controls escaped.
    async with running_hub() as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            with caplog.at_level(logging.INFO, logger="synapse.hub"):
                await websocket.send(
                    json.dumps(
                        {
                            "sender": "evil\nFORGED",
                            "type": "chat",
                            "target": "all",
                            "payload": "body\r\ninjected",
                        }
                    )
                )
                await read_until_type(websocket, "presence_update")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "evil\\nFORGED" in logged  # the sender's newline is escaped to \n
    assert "body\\r\\ninjected" in logged  # the payload's CR/LF is escaped
    assert "evil\nFORGED" not in logged  # never a raw newline forging a second line


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


@pytest.mark.parametrize("reserved_name", ["SynapseHub", "system", "SYNAPSE"])
async def test_reserved_protocol_identity_is_refused_end_to_end(reserved_name: str) -> None:
    async with running_hub() as (hub, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(
                json.dumps(
                    {
                        "sender": reserved_name,
                        "type": "heartbeat",
                        "takeover": True,
                        "lease": True,
                    }
                )
            )
            refused = await read_until_type(websocket, "name_conflict")
            with pytest.raises(ConnectionClosedError) as exc_info:
                await websocket.recv()

    assert refused["target"] == reserved_name
    assert "reserved for hub protocol provenance" in refused["payload"]
    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4009
    assert exc_info.value.rcvd.reason == "reserved identity"
    assert reserved_name not in hub.agent_sockets
