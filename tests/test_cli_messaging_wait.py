# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the messaging CLI commands (send/wait/listen)

from __future__ import annotations

import argparse
from typing import Any

import pytest

from cli_messaging_helpers import FakeAgent, _factory
from synapse_channel import cli_messaging


async def test_wait_returns_on_addressed_message(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "presence_update", "sender": "hub"},  # not a chat — ignored
        {"type": "chat", "sender": "A", "target": "B", "payload": "wake up"},
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
        uri="ws://h", name="B-rx", for_name="B", timeout=2.0, agent_factory=factory
    )
    assert code == 0
    assert "A: wake up" in capsys.readouterr().out


async def test_wait_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli_messaging._wait(
        uri="ws://h", name="B", for_name="B", timeout=1.0, agent_factory=factory
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_wait_times_out_with_nothing() -> None:
    holder: list[FakeAgent] = []
    # idle=True keeps the connection up so this exercises the timeout path (code 2),
    # distinct from a dropped connection (code 3).
    factory = _factory(holder, inbound=[], idle=True)
    code = await cli_messaging._wait(
        uri="ws://h", name="B", for_name="B", timeout=0.2, agent_factory=factory
    )
    assert code == 2


def test_cmd_wait_dispatches_with_for_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="X",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
    )
    assert cli_messaging._cmd_wait(ns) == 0


async def test_wait_ignores_own_messages() -> None:
    holder: list[FakeAgent] = []
    # A broadcast whose sender is our own identity (we send as for_name) must not wake us.
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "B", "target": "all", "payload": "x"}
    ]
    factory = _factory(holder, inbound=inbound, idle=True)
    code = await cli_messaging._wait(
        uri="ws://h", name="B-rx", for_name="B", timeout=0.2, agent_factory=factory
    )
    assert code == 2


async def test_wait_directed_only_ignores_broadcast() -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "all", "payload": "broadcast"}
    ]
    factory = _factory(holder, inbound=inbound, idle=True)
    code = await cli_messaging._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=0.2,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 2  # a broadcast does not wake in directed-only mode


async def test_wait_directed_only_wakes_on_named(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [{"type": "chat", "sender": "A", "target": "B", "payload": "p"}]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 0


def test_cmd_wait_derives_rx_name_for_bare_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_wait(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli_messaging, "_wait", fake_wait)
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="CEO",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
    )
    assert cli_messaging._cmd_wait(ns) == 0
    assert captured["name"] == "CEO-rx"  # bare identity gets a distinct receiver name
    assert captured["for_name"] == "CEO"


def test_cmd_wait_keeps_distinct_connect_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_wait(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli_messaging, "_wait", fake_wait)
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="CEO-rx",
        for_name="CEO",
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
    )
    assert cli_messaging._cmd_wait(ns) == 0
    assert captured["name"] == "CEO-rx"  # already distinct, left unchanged
    assert captured["for_name"] == "CEO"


async def test_wait_directed_only_wakes_on_ceo() -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "CEO", "target": "all", "payload": "directive"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 0  # a CEO broadcast wakes even a directed-only waiter


async def test_wait_directed_only_wakes_on_priority_broadcast() -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "all", "payload": "!", "priority": True}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 0  # a priority broadcast wakes even a directed-only waiter


async def test_wait_exits_when_connection_drops() -> None:
    holder: list[FakeAgent] = []
    # idle=False → connect() returns at once (the socket closed); no message arrives.
    # With timeout=0 the old loop hung forever on the dead socket; the waiter must now
    # exit with code 3 so the caller re-arms instead of going dark.
    factory = _factory(holder, inbound=[], idle=False)
    code = await cli_messaging._wait(
        uri="ws://h", name="X-rx", for_name="X", timeout=0.0, agent_factory=factory
    )
    assert code == 3


async def test_wait_jitters_on_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[float, float]] = []

    def _rec(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    monkeypatch.setattr("synapse_channel.cli_messaging.random.uniform", _rec)
    holder: list[FakeAgent] = []
    # A CEO broadcast (target "all") wakes a directed-only waiter — and woke every
    # other terminal too, so the exit is jittered.
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "CEO", "target": "all", "payload": "go"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        wake_jitter=5.0,
        agent_factory=factory,
    )
    assert code == 0
    assert calls == [(0.0, 5.0)]  # jitter applied for the broadcast


async def test_wait_no_jitter_on_directed_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[float, float]] = []

    def _rec(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    monkeypatch.setattr("synapse_channel.cli_messaging.random.uniform", _rec)
    holder: list[FakeAgent] = []
    # A 1:1 directed message (target == for_name) — no herd, so no jitter.
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "B", "payload": "hi"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
        uri="ws://h", name="B-rx", for_name="B", timeout=2.0, wake_jitter=5.0, agent_factory=factory
    )
    assert code == 0
    assert calls == []  # no jitter for a directed message
