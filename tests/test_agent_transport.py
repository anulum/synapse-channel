# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a transport owns the socket and the listener
"""The socket, the listener flag and the close diagnostics."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from websockets.asyncio.client import ClientConnection

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.agent_transport import MINIMUM_HEARTBEAT_INTERVAL, AgentTransport


def _transport(**overrides: Any) -> AgentTransport:
    values: dict[str, Any] = {
        "uri": "ws://hub",
        "token": None,
        "heartbeat_interval": 20.0,
        "ping_interval": 20.0,
        "ping_timeout": 20.0,
    }
    values.update(overrides)
    return AgentTransport(
        uri=values["uri"],
        token=values["token"],
        heartbeat_interval=values["heartbeat_interval"],
        ping_interval=values["ping_interval"],
        ping_timeout=values["ping_timeout"],
    )


def test_the_constructor_accepts_keywords_only() -> None:
    factory: Any = AgentTransport
    with pytest.raises(TypeError):
        factory("ws://hub", None, 20.0, 20.0, 20.0)


def test_the_heartbeat_interval_is_floored() -> None:
    assert _transport(heartbeat_interval=0.01).heartbeat_interval == MINIMUM_HEARTBEAT_INTERVAL
    assert _transport(heartbeat_interval=12.0).heartbeat_interval == 12.0


def test_an_attempt_attaches_a_socket_and_then_detaches_it() -> None:
    transport = _transport(token="secret")
    socket = cast(ClientConnection, object())
    transport.start_attempt("seat")
    transport.attach(socket)
    task: asyncio.Task[None] = cast(asyncio.Task[None], object())
    transport.bind_heartbeat(task)
    assert transport.connection is socket
    assert transport._heartbeat_task is task
    assert transport.token == "secret"
    transport.record_close(4008, "policy")
    transport.close_listener()
    assert transport.running is False
    assert transport.connection is None
    assert not transport.ready_event.is_set()
    assert transport._connect_active is True
    transport.release_attempt()
    assert transport._connect_active is False
    assert (transport.last_close_code, transport.last_close_reason) == (4008, "policy")


def test_a_second_attempt_is_refused_until_the_first_releases() -> None:
    transport = _transport()
    transport.start_attempt("seat")
    with pytest.raises(RuntimeError, match="seat: connect\\(\\) is already active"):
        transport.start_attempt("seat")
    transport.release_attempt()
    transport.start_attempt("seat")


def test_reset_rearms_the_listener_and_keeps_the_socket() -> None:
    transport = _transport()
    socket = cast(ClientConnection, object())
    transport.attach(socket)
    transport.running = False
    transport.ready_event.set()
    transport.record_close(1000, "bye")
    transport.reset_attempt()
    assert transport.running is True
    assert transport.connection is socket
    assert not transport.ready_event.is_set()
    assert transport.last_close_code is None
    assert transport.last_close_reason == ""


def test_the_agent_facade_reads_the_transport_and_keeps_three_setters() -> None:
    agent = SynapseAgent(
        "seat",
        verbose=False,
        machine_identity=False,
        uri="ws://hub",
        token="secret",
        heartbeat_interval=0.5,
        ping_interval=7.0,
        ping_timeout=9.0,
    )
    assert agent.uri == "ws://hub"
    assert agent.token == "secret"
    assert agent.heartbeat_interval == MINIMUM_HEARTBEAT_INTERVAL
    assert agent.ping_interval == 7.0
    assert agent.ping_timeout == 9.0
    assert agent.connection is None
    assert agent.ready_event is agent.transport.ready_event
    agent.running = False
    assert agent.transport.running is False
    agent.last_close_code = 4009
    agent.last_close_reason = "name conflict"
    assert agent.transport.last_close_code == 4009
    assert agent.transport.last_close_reason == "name conflict"
    for name in (
        "uri",
        "token",
        "connection",
        "heartbeat_interval",
        "ping_interval",
        "ping_timeout",
        "ready_event",
    ):
        with pytest.raises(AttributeError):
            setattr(agent, name, None)
