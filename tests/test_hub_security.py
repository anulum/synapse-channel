# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for routing hub security over real sockets

from __future__ import annotations

import asyncio

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import collect_available, read_json, read_until_type, running_hub, send_json
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import (
    InsecureBindError,
    SynapseHub,
    is_loopback_host,
)
from synapse_channel.core.hub_exposure import (
    InsecureBindError as ExposureInsecureBindError,
)
from synapse_channel.core.hub_exposure import (
    is_loopback_host as exposure_is_loopback_host,
)

# --- connect authentication --------------------------------------------------


def _close_code(exc: ConnectionClosed) -> int | None:
    if exc.rcvd is not None:
        return exc.rcvd.code
    if exc.sent is not None:
        return exc.sent.code
    return None


def _open_hub() -> SynapseHub:
    return SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test")


def _secured_hub(token: str = "s3cret") -> SynapseHub:
    return SynapseHub(
        default_ttl_seconds=300.0,
        hub_id="syn-test",
        authenticator=TokenAuthenticator([token]),
    )


async def test_open_hub_processes_without_a_token() -> None:
    async with running_hub(_open_hub()) as (_, uri):
        async with connect(uri) as websocket:
            assert (await read_json(websocket))["type"] == "welcome"
            await send_json(websocket, sender="A", type="chat", payload="hi")
            chat = await read_until_type(websocket, "chat")

    assert chat["payload"] == "hi"
    assert chat["sender"] == "A"


async def test_secured_hub_refuses_missing_token_and_closes() -> None:
    hub = _secured_hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender="A", type="chat", payload="hi")
            denied = await read_until_type(websocket, "auth_denied")
            assert "required" in denied["payload"]
            with pytest.raises(ConnectionClosed) as exc_info:
                await read_json(websocket)

    assert _close_code(exc_info.value) == 4010
    assert "A" not in hub.agent_sockets


async def test_secured_hub_refuses_bad_token() -> None:
    async with running_hub(_secured_hub()) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender="A", type="heartbeat", token="wrong")
            denied = await read_until_type(websocket, "auth_denied")

    assert "Invalid" in denied["payload"]


async def test_secured_hub_admits_valid_token_then_trusts_socket() -> None:
    hub = _secured_hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender="A", type="heartbeat", token="s3cret")
            assert (await read_until_type(websocket, "welcome"))["hub_id"] == "syn-test"
            await send_json(websocket, sender="A", type="chat", payload="hi")
            chat = await read_until_type(websocket, "chat")

    assert "A" not in hub.agent_sockets
    assert chat["payload"] == "hi"


async def test_secured_hub_enforces_per_agent_binding() -> None:
    hub = SynapseHub(hub_id="syn-test", authenticator=TokenAuthenticator({"tok": ["FAST"]}))
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender="REASON", type="heartbeat", token="tok")
            denied = await read_until_type(websocket, "auth_denied")

    assert "not authorised" in denied["payload"]


async def test_secured_hub_withholds_welcome_until_authenticated() -> None:
    async with running_hub(_secured_hub()) as (_, uri):
        async with connect(uri) as websocket:
            assert await collect_available(websocket, duration=0.05) == []
            await send_json(websocket, sender="A", type="heartbeat", token="s3cret")
            assert (await read_until_type(websocket, "welcome"))["hub_id"] == "syn-test"


async def test_secured_hub_welcomes_after_authentication_once() -> None:
    async with running_hub(_secured_hub()) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender="A", type="heartbeat", token="s3cret")
            await read_until_type(websocket, "welcome")
            await send_json(websocket, sender="A", type="chat", payload="hi")
            await read_until_type(websocket, "chat")
            messages = await collect_available(websocket, duration=0.05)

    assert [m["type"] for m in messages].count("welcome") == 0


async def test_open_hub_still_welcomes_on_connect() -> None:
    async with running_hub(_open_hub()) as (_, uri):
        async with connect(uri) as websocket:
            assert (await read_json(websocket))["type"] == "welcome"


async def test_authentication_times_out_idle_socket() -> None:
    hub = SynapseHub(
        hub_id="syn-test",
        authenticator=TokenAuthenticator(["s3cret"]),
        auth_timeout=0.05,
    )
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            with pytest.raises(ConnectionClosed) as exc_info:
                await read_json(websocket)

    assert _close_code(exc_info.value) == 4012


async def test_handler_secured_hub_processes_after_auth() -> None:
    hub = _secured_hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender="A", type="heartbeat", token="s3cret")
            await read_until_type(websocket, "welcome")
            await send_json(websocket, sender="A", type="chat", payload="hi")
            chat = await read_until_type(websocket, "chat")
        assert hub.unauth_clients == set()

    assert chat["payload"] == "hi"


async def test_handler_secured_hub_closes_on_failed_auth() -> None:
    hub = _secured_hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender="A", type="chat", payload="hi")
            await read_until_type(websocket, "auth_denied")
            with pytest.raises(ConnectionClosed):
                await read_json(websocket)
        for _ in range(20):
            if hub.unauth_clients == set():
                break
            await asyncio.sleep(0.01)
        assert hub.unauth_clients == set()
        assert "A" not in hub.agent_sockets


def test_is_loopback_host_recognises_loopback_addresses() -> None:
    assert is_loopback_host("localhost")
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("  LOCALHOST ")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("10.0.0.5")


def test_hub_reexports_exposure_helpers_for_compatibility() -> None:
    assert is_loopback_host is exposure_is_loopback_host
    assert InsecureBindError is ExposureInsecureBindError


def test_exposure_problems_empty_on_loopback() -> None:
    assert _open_hub()._exposure_problems("localhost") == []
    assert _open_hub()._exposure_problems("127.0.0.1") == []


def test_exposure_problems_flags_missing_token_off_loopback() -> None:
    problems = _open_hub()._exposure_problems("0.0.0.0")
    assert len(problems) == 1
    assert "no token" in problems[0]


def test_exposure_problems_empty_when_token_set() -> None:
    assert _secured_hub()._exposure_problems("0.0.0.0") == []


def test_guard_exposure_refuses_off_loopback_without_token() -> None:
    with pytest.raises(InsecureBindError, match="Refusing to bind"):
        _open_hub()._guard_exposure("0.0.0.0")


def test_guard_exposure_silent_on_loopback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="synapse.hub"):
        _open_hub()._guard_exposure("localhost")
    assert caplog.records == []


def test_guard_exposure_passes_when_token_set(caplog: pytest.LogCaptureFixture) -> None:
    # A token still binds off loopback, but over plaintext ws:// it now carries
    # the transport advisory instead of silence (never a refusal).
    with caplog.at_level("WARNING", logger="synapse.hub"):
        _secured_hub()._guard_exposure("0.0.0.0")
    assert len(caplog.records) == 1
    assert "plaintext ws://" in caplog.text


def test_guard_exposure_silent_when_token_rides_tls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="synapse.hub"):
        _secured_hub()._guard_exposure("0.0.0.0", tls_active=True)
    assert caplog.records == []


def test_guard_exposure_warns_instead_of_refusing_when_overridden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SynapseHub(insecure_off_loopback=True)
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub._guard_exposure("0.0.0.0")
    assert "no token" in caplog.text


async def test_takeover_evicts_stale_holder(caplog: pytest.LogCaptureFixture) -> None:
    hub = _open_hub()
    with caplog.at_level("INFO", logger="synapse.hub"):
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as old, connect(uri) as new:
                await read_until_type(old, "welcome")
                await send_json(old, sender="X-rx", type="heartbeat", payload="online")
                await read_until_type(old, "presence_update")
                await read_until_type(new, "welcome")
                await send_json(
                    new, sender="X-rx", type="heartbeat", payload="online", takeover=True
                )
                with pytest.raises(ConnectionClosed) as exc_info:
                    await read_json(old)
                await send_json(new, sender="X-rx", type="chat", payload="still online")
                chat = await read_until_type(new, "chat")

    assert _close_code(exc_info.value) == 4010
    assert chat["payload"] == "still online"
    assert "takeover accepted sender=X-rx" in caplog.text
    assert "reason=superseded" in caplog.text


async def test_name_conflict_without_takeover_rejects_newcomer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="synapse.hub"):
        async with running_hub(_open_hub()) as (_, uri):
            async with connect(uri) as old, connect(uri) as new:
                await read_until_type(old, "welcome")
                await send_json(old, sender="Y", type="heartbeat", payload="online")
                await read_until_type(old, "presence_update")
                await read_until_type(new, "welcome")
                await send_json(new, sender="Y", type="heartbeat", payload="online")
                conflict = await read_until_type(new, "name_conflict")
                with pytest.raises(ConnectionClosed) as exc_info:
                    await read_json(new)
                await send_json(old, sender="Y", type="chat", payload="original")
                chat = await read_until_type(old, "chat")

    assert conflict["target"] == "Y"
    assert _close_code(exc_info.value) == 4009
    assert chat["payload"] == "original"
    assert "name conflict sender=Y" in caplog.text
    assert "reason=name conflict" in caplog.text
