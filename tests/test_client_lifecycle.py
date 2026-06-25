# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the async hub client using an injected transport

from __future__ import annotations

import json
from typing import Any

import pytest

from client_helpers import FakeConnect, FakeWebSocket, _install_connection
from synapse_channel.client import agent as client_module
from synapse_channel.client.agent import DEFAULT_HUB_URI, MINIMUM_HEARTBEAT_INTERVAL, SynapseAgent


def test_defaults_and_heartbeat_clamp() -> None:
    agent = SynapseAgent("A")
    assert agent.uri == DEFAULT_HUB_URI
    assert agent.heartbeat_interval == 20.0
    agent_fast = SynapseAgent("B", heartbeat_interval=1.0)
    assert agent_fast.heartbeat_interval == MINIMUM_HEARTBEAT_INTERVAL


def test_ping_keepalive_defaults() -> None:
    agent = SynapseAgent("A")
    assert agent.ping_interval == 20.0
    assert agent.ping_timeout == 20.0


async def test_connect_passes_ping_keepalive(monkeypatch: pytest.MonkeyPatch) -> None:
    # The client must hand explicit ping keepalive to the transport so a half-open
    # socket (killed hub, ungraceful restart, undelivered eviction) is reaped rather
    # than blocking the waiter for days.
    seen: dict[str, object] = {}

    def spy(uri: str, **kwargs: object) -> FakeConnect:
        seen.update(kwargs)
        return FakeConnect(FakeWebSocket([json.dumps({"type": "welcome", "hub_id": "h"})]))

    monkeypatch.setattr(client_module, "connect", spy)
    agent = SynapseAgent("A", ping_interval=7.0, ping_timeout=9.0, verbose=False)
    await agent.connect()
    assert seen == {"ping_interval": 7.0, "ping_timeout": 9.0}


async def test_connect_registers_dispatches_and_filters_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        received.append(data)

    welcome = json.dumps({"type": "welcome", "hub_id": "syn-xyz"})
    self_echo = json.dumps({"type": "chat", "sender": "A", "payload": "mine"})
    peer = json.dumps({"type": "chat", "sender": "B", "payload": "hi"})
    bad = "this-is-not-json"
    ws = FakeWebSocket([welcome, self_echo, peer, bad])
    _install_connection(monkeypatch, ws)

    agent = SynapseAgent("A", callback, verbose=True)
    await agent.connect()

    # Registration heartbeat was sent first.
    first = json.loads(ws.sent[0])
    assert first["type"] == "heartbeat"
    assert first["sender"] == "A"
    # Welcome populated hub id and ready state.
    assert agent.hub_id == "syn-xyz"
    assert agent.ready_event.is_set()
    # The peer message reached the callback; the self echo and bad JSON did not.
    assert [d.get("payload") for d in received if d.get("type") == "chat"] == ["hi"]
    # Listener exited cleanly.
    assert agent.running is False
    assert agent.connection is None


async def test_connect_without_callback_still_processes_welcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    ws = FakeWebSocket([welcome])
    _install_connection(monkeypatch, ws)

    agent = SynapseAgent("A", None)
    await agent.connect()
    assert agent.hub_id == "h"


async def test_connect_quiet_skips_lifecycle_prints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    bad = "not-json"
    ws = FakeWebSocket([welcome, bad])
    _install_connection(monkeypatch, ws)

    agent = SynapseAgent("A", verbose=False)
    await agent.connect()
    out = capsys.readouterr().out
    assert "connected to Synapse" not in out
    assert "malformed JSON" not in out


async def test_connect_stops_when_running_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        seen.append(data)
        agent.running = False  # stop after the first dispatched message

    first = json.dumps({"type": "chat", "sender": "B", "payload": "1"})
    second = json.dumps({"type": "chat", "sender": "B", "payload": "2"})
    ws = FakeWebSocket([first, second])
    _install_connection(monkeypatch, ws)

    agent = SynapseAgent("A", callback)
    await agent.connect()
    assert [d["payload"] for d in seen] == ["1"]


async def test_connect_handles_connection_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(uri: str, **kwargs: object) -> FakeConnect:
        raise ConnectionRefusedError

    monkeypatch.setattr(client_module, "connect", refuse)
    agent = SynapseAgent("A", verbose=True)
    await agent.connect()
    assert "could not connect" in capsys.readouterr().out


async def test_connect_refused_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(uri: str, **kwargs: object) -> FakeConnect:
        raise ConnectionRefusedError

    monkeypatch.setattr(client_module, "connect", refuse)
    agent = SynapseAgent("A", verbose=False)
    await agent.connect()
    assert capsys.readouterr().out == ""


async def test_connect_handles_transport_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(uri: str, **kwargs: object) -> FakeConnect:
        raise OSError("reset")

    monkeypatch.setattr(client_module, "connect", boom)
    agent = SynapseAgent("A")
    await agent.connect()
    assert "Connection lost" in capsys.readouterr().out
