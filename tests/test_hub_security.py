# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routing hub using fake server sockets

from __future__ import annotations

import pytest

from hub_helpers import FakeServerWS, _hub, _msg
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import (
    InsecureBindError,
    SynapseHub,
    is_loopback_host,
)

# --- connect authentication --------------------------------------------------


def _secured_hub(token: str = "s3cret") -> SynapseHub:
    return SynapseHub(
        default_ttl_seconds=300.0, hub_id="syn-test", authenticator=TokenAuthenticator([token])
    )


async def test_open_hub_processes_without_a_token() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="hi"), ws)
    assert any(m.get("type") == "chat" for m in ws.decoded())


async def test_secured_hub_refuses_missing_token_and_closes() -> None:
    hub = _secured_hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="hi"), ws)
    assert ws.last()["type"] == "auth_denied"
    assert "required" in ws.last()["payload"]
    assert ws.closed == (4010, "auth denied")
    assert "A" not in hub.agent_sockets  # never bound


async def test_secured_hub_refuses_bad_token() -> None:
    hub = _secured_hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="heartbeat", token="wrong"), ws)
    assert ws.last()["type"] == "auth_denied"
    assert "Invalid" in ws.last()["payload"]


async def test_secured_hub_admits_valid_token_then_trusts_socket() -> None:
    hub = _secured_hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="heartbeat", token="s3cret"), ws)
    assert "A" in hub.agent_sockets  # bound after authenticating
    # A later message on the same socket need not re-present the token.
    await hub.handle_message(_msg(sender="A", type="chat", payload="hi"), ws)
    assert any(m.get("type") == "chat" for m in ws.decoded())


async def test_secured_hub_enforces_per_agent_binding() -> None:
    hub = SynapseHub(hub_id="syn-test", authenticator=TokenAuthenticator({"tok": ["FAST"]}))
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="REASON", type="heartbeat", token="tok"), ws)
    assert ws.last()["type"] == "auth_denied"
    assert "not authorised" in ws.last()["payload"]


async def test_secured_hub_withholds_welcome_until_authenticated() -> None:
    # A secured hub must not leak the roster or connection count to an
    # unauthenticated socket: register() sends nothing.
    hub = _secured_hub()
    hub.agent_sockets["SECRET-PEER"] = object()  # someone already online
    ws = FakeServerWS()
    await hub.register(ws)
    assert ws.sent == []  # no welcome, no roster
    assert ws in hub.connected_clients  # still counted (bounded by auth timeout)


async def test_secured_hub_welcomes_after_authentication() -> None:
    hub = _secured_hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="heartbeat", token="s3cret"), ws)
    welcomes = [m for m in ws.decoded() if m.get("type") == "welcome"]
    assert len(welcomes) == 1  # welcome sent exactly once, after auth
    assert "A" in hub.agent_sockets
    # A second authenticated frame does not re-welcome.
    await hub.handle_message(_msg(sender="A", type="chat", payload="hi"), ws)
    assert len([m for m in ws.decoded() if m.get("type") == "welcome"]) == 1


async def test_open_hub_still_welcomes_on_connect() -> None:
    hub = _hub()  # no authenticator
    ws = FakeServerWS()
    await hub.register(ws)
    assert ws.last()["type"] == "welcome"


async def test_authenticate_or_close_times_out_idle_socket() -> None:
    hub = SynapseHub(
        hub_id="syn-test", authenticator=TokenAuthenticator(["s3cret"]), auth_timeout=0.05
    )
    ws = FakeServerWS(recv_blocks=True)
    await hub.register(ws)
    assert await hub._authenticate_or_close(ws) is False
    assert ws.closed == (4012, "auth timeout")


async def test_authenticate_or_close_rejects_unauthenticated_first_frame() -> None:
    hub = _secured_hub()
    ws = FakeServerWS([_msg(sender="A", type="chat", payload="hi")])  # no token
    await hub.register(ws)
    assert await hub._authenticate_or_close(ws) is False
    assert ws.closed is not None
    assert "A" not in hub.agent_sockets


async def test_authenticate_or_close_admits_valid_first_frame() -> None:
    hub = _secured_hub()
    ws = FakeServerWS([_msg(sender="A", type="heartbeat", token="s3cret")])
    await hub.register(ws)
    assert await hub._authenticate_or_close(ws) is True
    assert "A" in hub.agent_sockets
    assert any(m.get("type") == "welcome" for m in ws.decoded())


async def test_authenticate_or_close_handles_immediate_disconnect() -> None:
    hub = _secured_hub()
    ws = FakeServerWS()  # empty: recv() raises ConnectionClosed
    await hub.register(ws)
    assert await hub._authenticate_or_close(ws) is False


async def test_handler_secured_hub_processes_after_auth() -> None:
    hub = _secured_hub()
    ws = FakeServerWS(
        [
            _msg(sender="A", type="heartbeat", token="s3cret"),
            _msg(sender="A", type="chat", payload="hi"),
        ]
    )
    await hub.handler(ws)
    types = [m.get("type") for m in ws.decoded()]
    assert "welcome" in types  # welcomed only after the authenticated first frame
    assert any(m.get("type") == "chat" and m.get("payload") == "hi" for m in ws.decoded())
    assert ws not in hub.connected_clients  # unregistered at the end
    assert hub.unauth_clients == set()  # released its pre-auth slot once authenticated


async def test_handler_secured_hub_closes_on_failed_auth() -> None:
    hub = _secured_hub()
    ws = FakeServerWS([_msg(sender="A", type="chat", payload="hi")])  # no token
    await hub.handler(ws)
    # The unauthenticated first frame ends the connection; the agent never binds.
    assert ws not in hub.connected_clients
    assert "A" not in hub.agent_sockets
    assert hub.unauth_clients == set()  # the pre-auth slot is released even on failure


def test_is_loopback_host_recognises_loopback_addresses() -> None:
    assert is_loopback_host("localhost")
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("  LOCALHOST ")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("10.0.0.5")


def test_exposure_problems_empty_on_loopback() -> None:
    assert _hub()._exposure_problems("localhost") == []
    assert _hub()._exposure_problems("127.0.0.1") == []


def test_exposure_problems_flags_missing_token_off_loopback() -> None:
    problems = _hub()._exposure_problems("0.0.0.0")
    assert len(problems) == 1
    assert "no token" in problems[0]


def test_exposure_problems_empty_when_token_set() -> None:
    assert _secured_hub()._exposure_problems("0.0.0.0") == []


def test_guard_exposure_refuses_off_loopback_without_token() -> None:
    hub = _hub()  # no authenticator
    with pytest.raises(InsecureBindError, match="Refusing to bind"):
        hub._guard_exposure("0.0.0.0")


def test_guard_exposure_silent_on_loopback(caplog: pytest.LogCaptureFixture) -> None:
    hub = _hub()
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub._guard_exposure("localhost")  # does not raise
    assert caplog.records == []


def test_guard_exposure_passes_when_token_set(caplog: pytest.LogCaptureFixture) -> None:
    hub = _secured_hub()
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub._guard_exposure("0.0.0.0")  # does not raise
    assert caplog.records == []


def test_guard_exposure_warns_instead_of_refusing_when_overridden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SynapseHub(insecure_off_loopback=True)  # no authenticator
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub._guard_exposure("0.0.0.0")  # warns, does not raise
    assert "no token" in caplog.text


async def test_takeover_evicts_stale_holder() -> None:
    hub = _hub()
    ws_old = FakeServerWS()
    ws_new = FakeServerWS()
    await hub.handle_message(_msg(sender="X-rx", type="heartbeat", payload="online"), ws_old)
    assert hub.agent_sockets["X-rx"] is ws_old
    # The re-arming socket takes over the name: the stale holder is closed (4010)
    # and the name rebinds to the newcomer.
    await hub.handle_message(
        _msg(sender="X-rx", type="heartbeat", payload="online", takeover=True), ws_new
    )
    assert ws_old.closed == (4010, "superseded")
    assert hub.agent_sockets["X-rx"] is ws_new
    assert hub.socket_agent.get(ws_old) is None


async def test_name_conflict_without_takeover_rejects_newcomer() -> None:
    hub = _hub()
    ws_old = FakeServerWS()
    ws_new = FakeServerWS()
    await hub.handle_message(_msg(sender="Y", type="heartbeat", payload="online"), ws_old)
    await hub.handle_message(_msg(sender="Y", type="heartbeat", payload="online"), ws_new)
    assert ws_new.closed == (4009, "name conflict")
    assert hub.agent_sockets["Y"] is ws_old  # the original holder is untouched


async def test_takeover_tolerates_a_failing_close() -> None:
    hub = _hub()

    class _RaisingClose(FakeServerWS):
        async def close(self, code: int = 1000, reason: str = "") -> None:
            raise OSError("socket already gone")

    ws_old = _RaisingClose()
    ws_new = FakeServerWS()
    await hub.handle_message(_msg(sender="Z-rx", type="heartbeat", payload="online"), ws_old)
    # The stale holder's close() raises, but takeover still rebinds the name.
    await hub.handle_message(
        _msg(sender="Z-rx", type="heartbeat", payload="online", takeover=True), ws_new
    )
    assert hub.agent_sockets["Z-rx"] is ws_new
