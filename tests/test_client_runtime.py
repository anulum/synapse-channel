# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the async hub client using an injected transport

from __future__ import annotations

import json

import pytest

from client_helpers import FakeWebSocket
from synapse_channel.client.agent import SynapseAgent


async def test_heartbeat_tick_noop_without_connection() -> None:
    agent = SynapseAgent("A")
    await agent._heartbeat_tick()  # no connection -> no error, nothing sent


async def test_heartbeat_tick_sends_when_connected() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent._heartbeat_tick()
    assert json.loads(ws.sent[0])["payload"] == "alive"


async def test_heartbeat_loop_runs_one_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]

    async def fake_sleep(_seconds: float) -> None:
        agent.running = False  # end the loop after the first sleep

    monkeypatch.setattr("synapse_channel.client.agent.asyncio.sleep", fake_sleep)
    await agent._heartbeat_loop()
    assert json.loads(ws.sent[0])["payload"] == "alive"


async def test_wait_until_ready_true_when_set() -> None:
    agent = SynapseAgent("A")
    agent.ready_event.set()
    assert await agent.wait_until_ready(timeout=0.1) is True


async def test_wait_until_ready_times_out() -> None:
    agent = SynapseAgent("A")
    assert await agent.wait_until_ready(timeout=0.1) is False


def test_start_runs_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = {"value": False}

    async def fake_connect(self: SynapseAgent) -> None:
        ran["value"] = True

    monkeypatch.setattr(SynapseAgent, "connect", fake_connect)
    SynapseAgent("A").start()
    assert ran["value"] is True


def test_start_swallows_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_connect(self: SynapseAgent) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(SynapseAgent, "connect", fake_connect)
    SynapseAgent("A", verbose=True).start()
    assert "Shutting down" in capsys.readouterr().out


def test_start_keyboard_interrupt_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_connect(self: SynapseAgent) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(SynapseAgent, "connect", fake_connect)
    SynapseAgent("A", verbose=False).start()
    assert capsys.readouterr().out == ""
