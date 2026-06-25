# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for routing hub runtime and HTTP probes

from __future__ import annotations

import asyncio
import json
import signal
from collections.abc import Callable

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import http_get, read_json, read_until_type, running_hub
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import InsecureBindError, SynapseHub

# --- Sprint A: caps, capacity gate, takeover cooldown, signal handlers -------


def _close_code(exc: ConnectionClosed) -> int | None:
    if exc.rcvd is not None:
        return exc.rcvd.code
    if exc.sent is not None:
        return exc.sent.code
    return None


def test_hub_caps_clamped() -> None:
    hub = SynapseHub(max_clients=0, max_msg_bytes=0, takeover_cooldown=-5.0)
    assert hub.max_clients == 1
    assert hub.max_msg_bytes == 1
    assert hub.takeover_cooldown == 0.0


async def test_handler_rejects_at_capacity() -> None:
    hub = SynapseHub(max_clients=1)
    hub.connected_clients.add(object())
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            with pytest.raises(ConnectionClosed) as exc_info:
                await read_json(websocket)

    assert _close_code(exc_info.value) == 4013


async def test_handler_rejects_when_unauth_cap_reached() -> None:
    hub = SynapseHub(authenticator=TokenAuthenticator(["t"]), max_unauth_clients=1)
    hub.unauth_clients.add(object())
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            with pytest.raises(ConnectionClosed) as exc_info:
                await read_json(websocket)

    assert _close_code(exc_info.value) == 4014
    assert hub.connected_clients == set()


async def test_secured_hub_handles_disconnect_before_auth_frame() -> None:
    hub = SynapseHub(authenticator=TokenAuthenticator(["t"]), auth_timeout=1.0)
    async with running_hub(hub) as (_, uri):
        websocket = await connect(uri)
        await websocket.close()
        await asyncio.sleep(0.05)

    assert hub.unauth_clients == set()
    assert hub.connected_clients == set()


def test_max_unauth_clients_defaults_to_max_clients_and_clamps() -> None:
    assert SynapseHub(max_clients=20).max_unauth_clients == 20
    assert SynapseHub(max_clients=20, max_unauth_clients=5).max_unauth_clients == 5
    assert SynapseHub(max_unauth_clients=0).max_unauth_clients == 1


async def test_takeover_cooldown_blocks_rapid_eviction() -> None:
    clock = [100.0]
    hub = SynapseHub(takeover_cooldown=2.0, clock=lambda: clock[0])
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as first, connect(uri) as second, connect(uri) as third:
            await read_until_type(first, "welcome")
            await first.send(json.dumps({"sender": "A", "type": "heartbeat"}))
            await read_until_type(first, "presence_update")
            await read_until_type(second, "welcome")
            await second.send(json.dumps({"sender": "A", "type": "heartbeat", "takeover": True}))
            with pytest.raises(ConnectionClosed) as first_close:
                await read_json(first)

            clock[0] = 101.0
            await read_until_type(third, "welcome")
            await third.send(json.dumps({"sender": "A", "type": "heartbeat", "takeover": True}))
            for _ in range(5):
                try:
                    await read_json(third)
                except ConnectionClosed as closed:
                    third_close = closed
                    break
            else:
                pytest.fail("third takeover connection did not close")

    assert _close_code(first_close.value) == 4010
    assert _close_code(third_close) == 4014


def test_install_signal_handlers_wires_both() -> None:
    hub = SynapseHub()
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    try:
        hub._install_signal_handlers(loop, stop)
        assert loop.remove_signal_handler(signal.SIGTERM) is True
        assert loop.remove_signal_handler(signal.SIGINT) is True
    finally:
        loop.close()


def test_install_signal_handlers_suppresses_unsupported() -> None:
    hub = SynapseHub()

    class UnsupportedSignalLoop(asyncio.SelectorEventLoop):
        def add_signal_handler(
            self, sig: int, callback: Callable[..., object], *args: object
        ) -> None:
            raise NotImplementedError

    loop = UnsupportedSignalLoop()
    try:
        hub._install_signal_handlers(loop, asyncio.Event())
    finally:
        loop.close()


# --- HTTP /metrics and /health endpoints -------------------------------------


def test_metrics_disabled_by_default() -> None:
    assert SynapseHub().enable_metrics is False


def test_uptime_seconds_is_elapsed_and_never_negative() -> None:
    forward = iter([10.0, 13.5])
    hub = SynapseHub(clock=lambda: next(forward))
    assert hub.uptime_seconds() == 3.5
    backward = iter([10.0, 9.0])
    hub2 = SynapseHub(clock=lambda: next(backward))
    assert hub2.uptime_seconds() == 0.0


async def test_process_request_serves_prometheus_metrics() -> None:
    async with running_hub(SynapseHub(enable_metrics=True)) as (_, uri):
        status, headers, body = await http_get(uri, "/metrics")

    assert status == 200
    assert headers["Content-Type"].startswith("text/plain")
    assert headers["Content-Length"] == str(len(body.encode("utf-8")))
    assert "synapse_up 1" in body


async def test_process_request_serves_health_json() -> None:
    async with running_hub(SynapseHub(hub_id="syn-probe", enable_metrics=True)) as (_, uri):
        status, headers, body = await http_get(uri, "/health")

    assert status == 200
    assert headers["Content-Type"] == "application/json"
    parsed = json.loads(body)
    assert parsed["status"] == "ok"
    assert parsed["hub_id"] == "syn-probe"


async def test_process_request_ignores_a_query_string() -> None:
    async with running_hub(SynapseHub(enable_metrics=True)) as (_, uri):
        status, _, body = await http_get(uri, "/metrics?step=15s")

    assert status == 200
    assert "synapse_up 1" in body


async def test_process_request_falls_through_for_websocket_paths() -> None:
    async with running_hub(SynapseHub(enable_metrics=True)) as (_, uri):
        async with connect(f"{uri}/socket") as websocket:
            assert (await read_until_type(websocket, "welcome"))["type"] == "welcome"


async def test_metrics_token_rejects_a_request_without_the_token() -> None:
    async with running_hub(SynapseHub(enable_metrics=True, metrics_token="m3tr1c")) as (_, uri):
        status, headers, body = await http_get(uri, "/metrics")

    assert status == 401
    assert headers["WWW-Authenticate"].startswith("Bearer")
    assert "synapse_up" not in body


async def test_metrics_token_admits_a_bearer_header() -> None:
    async with running_hub(SynapseHub(enable_metrics=True, metrics_token="m3tr1c")) as (_, uri):
        status, _, body = await http_get(uri, "/metrics", authorization="Bearer m3tr1c")

    assert status == 200
    assert "synapse_up 1" in body


async def test_metrics_token_rejects_a_query_string_token_by_default() -> None:
    async with running_hub(SynapseHub(enable_metrics=True, metrics_token="m3tr1c")) as (_, uri):
        status, _, _ = await http_get(uri, "/metrics?token=m3tr1c")

    assert status == 401


async def test_metrics_token_admits_a_query_string_token_when_opted_in() -> None:
    hub = SynapseHub(enable_metrics=True, metrics_token="m3tr1c", metrics_query_token_ok=True)
    async with running_hub(hub) as (_, uri):
        status, _, _ = await http_get(uri, "/metrics?token=m3tr1c")
        multi, _, _ = await http_get(uri, "/metrics?foo=bar&token=m3tr1c")
        missing, _, _ = await http_get(uri, "/metrics?foo=bar")
        bearer, _, _ = await http_get(uri, "/metrics", authorization="Bearer m3tr1c")

    assert status == 200
    assert multi == 200
    assert missing == 401
    assert bearer == 200


async def test_metrics_token_rejects_a_wrong_token() -> None:
    async with running_hub(SynapseHub(enable_metrics=True, metrics_token="m3tr1c")) as (_, uri):
        status, _, _ = await http_get(uri, "/health", authorization="Bearer nope")

    assert status == 401


def test_guard_exposure_refuses_unauthenticated_metrics_off_loopback() -> None:
    hub = SynapseHub(authenticator=TokenAuthenticator(["t"]), enable_metrics=True)
    with pytest.raises(InsecureBindError, match="metrics"):
        hub._guard_exposure("0.0.0.0")


def test_guard_exposure_warns_on_unauthenticated_metrics_when_overridden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SynapseHub(
        authenticator=TokenAuthenticator(["t"]),
        enable_metrics=True,
        insecure_off_loopback=True,
    )
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub._guard_exposure("0.0.0.0")
    assert any("metrics" in r.message for r in caplog.records)


def test_guard_exposure_passes_when_metrics_token_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SynapseHub(
        authenticator=TokenAuthenticator(["t"]), enable_metrics=True, metrics_token="m"
    )
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub._guard_exposure("0.0.0.0")
    assert caplog.records == []
