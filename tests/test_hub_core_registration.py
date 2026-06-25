# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - tests for hub registration and input guards

from __future__ import annotations

from typing import Any

from hub_helpers import FakeServerWS, _hub, _msg
from synapse_channel.core.hub import (
    MAX_LOG_PAYLOAD,
    SynapseHub,
)
from synapse_channel.core.ratelimit import RateLimiter


async def test_register_sends_welcome() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    welcome = ws.last()
    assert welcome["type"] == "welcome"
    assert welcome["hub_id"] == "syn-test"
    assert ws in hub.connected_clients


def test_redact_payload_truncates_a_long_payload() -> None:
    assert SynapseHub._redact_payload("short") == "short"
    long = "x" * 500
    redacted = SynapseHub._redact_payload(long)
    assert redacted.startswith("x" * MAX_LOG_PAYLOAD)
    assert f"(+{500 - MAX_LOG_PAYLOAD} chars)" in redacted


async def test_malformed_json_returns_error() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.handle_message("{not json", ws)
    assert ws.last()["type"] == "error"
    assert "Malformed JSON" in ws.last()["payload"]


async def test_deeply_nested_json_is_rejected_not_crashed() -> None:
    # A frame nested far past the depth guard must be refused as malformed, never
    # drive the decoder into a RecursionError that would tear down the connection.
    hub = _hub()
    ws = FakeServerWS()
    await hub.handle_message("[" * 500 + "]" * 500, ws)
    assert ws.last()["type"] == "error"
    assert "Malformed JSON" in ws.last()["payload"]


async def test_host_rate_limiter_refuses_a_flooding_host() -> None:
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=2.0))
    ws = FakeServerWS(remote_address=("10.0.0.9", 5555))
    await hub.handle_message(_msg(sender="A", type="chat", payload="1"), ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="2"), ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="3"), ws)
    assert ws.last()["type"] == "error"
    assert "Host rate limit exceeded" in ws.last()["payload"]


async def test_host_rate_limiter_meters_heartbeats() -> None:
    # Unlike the per-agent limiter (which skips heartbeats), the per-host ceiling
    # charges them, so a bare-heartbeat flood from one host is bounded.
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=1.0))
    ws = FakeServerWS(remote_address=("10.0.0.9", 5555))
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)
    assert ws.last()["type"] == "error"
    assert "Host rate limit exceeded" in ws.last()["payload"]


async def test_host_rate_limiter_budgets_hosts_independently() -> None:
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=1.0))
    ws1 = FakeServerWS(remote_address=("10.0.0.1", 1))
    ws2 = FakeServerWS(remote_address=("10.0.0.2", 2))
    await hub.handle_message(_msg(sender="A", type="chat", payload="x"), ws1)
    await hub.handle_message(_msg(sender="A", type="chat", payload="x"), ws1)  # ws1 over its budget
    await hub.handle_message(_msg(sender="B", type="chat", payload="y"), ws2)  # ws2 fresh budget
    assert any("Host rate limit" in raw for raw in ws1.sent)
    assert not any("Host rate limit" in raw for raw in ws2.sent)


def test_remote_host_handles_tuple_bare_and_missing() -> None:
    class _WS:
        def __init__(self, addr: Any) -> None:
            self.remote_address = addr

    assert SynapseHub._remote_host(_WS(("1.2.3.4", 9))) == "1.2.3.4"
    assert SynapseHub._remote_host(_WS("sock-path")) == "sock-path"
    assert SynapseHub._remote_host(_WS(None)) == "unknown"
    assert SynapseHub._remote_host(object()) == "unknown"


async def test_anonymous_sender_gets_generated_name() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(type="heartbeat"), ws)
    assert any(name.startswith("anon-") for name in hub.agent_sockets)
