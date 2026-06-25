# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the messaging CLI commands (send/wait/listen)

from __future__ import annotations

import argparse
import asyncio
from contextlib import AbstractAsyncContextManager

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_messaging
from synapse_channel.core.hub import SynapseHub


async def _wait_for_presence(observer: AgentHandle, name: str) -> None:
    await observer.recorder.wait_for(
        lambda message: message.get("type") == "presence_update" and message.get("agent") == name
    )


async def _send_chat(
    uri: str, sender: str, target: str, payload: str, *, priority: bool = False
) -> None:
    handle = await connect_agent(sender, uri)
    try:
        await handle.agent.chat(payload, target=target, priority=priority)
    finally:
        await close_agents(handle)


async def test_wait_returns_on_addressed_message(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(uri=uri, name="B-rx", for_name="B", timeout=2.0)
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "B", "wake up")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0
    assert "A: wake up" in capsys.readouterr().out


async def test_wait_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_messaging._wait(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="B",
        for_name="B",
        timeout=1.0,
        ready_timeout=0.1,
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_wait_times_out_with_nothing() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        code = await cli_messaging._wait(
            uri=uri,
            name="B-rx",
            for_name="B",
            timeout=0.05,
            poll_interval=0.01,
        )
    assert code == 2


def test_cmd_wait_dispatches_with_for_default(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="X",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_wait(ns) == 1
    assert "[X-rx] Could not reach hub" in capsys.readouterr().out


async def test_wait_ignores_own_messages() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=0.05,
                poll_interval=0.01,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "B", "all", "x")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 2


async def test_wait_directed_only_ignores_broadcast() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=0.05,
                directed_only=True,
                poll_interval=0.01,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "all", "broadcast")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 2  # a broadcast does not wake in directed-only mode


async def test_wait_directed_only_wakes_on_named(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                directed_only=True,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "B", "p")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0
    assert "A: p" in capsys.readouterr().out


def test_cmd_wait_derives_rx_name_for_bare_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="CEO",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_wait(ns) == 1
    assert "[CEO-rx] Could not reach hub" in capsys.readouterr().out


def test_cmd_wait_keeps_distinct_connect_name(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="CEO-rx",
        for_name="CEO",
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_wait(ns) == 1
    assert "[CEO-rx] Could not reach hub" in capsys.readouterr().out


async def test_wait_directed_only_wakes_on_ceo() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                directed_only=True,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "CEO", "all", "directive")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0  # a CEO broadcast wakes even a directed-only waiter


async def test_wait_directed_only_wakes_on_priority_broadcast() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                directed_only=True,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "all", "!", priority=True)
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0  # a priority broadcast wakes even a directed-only waiter


async def test_wait_exits_when_connection_drops() -> None:
    manager: AbstractAsyncContextManager[tuple[SynapseHub, str]] = running_hub(SynapseHub())
    _hub, uri = await manager.__aenter__()
    observer = await connect_agent("OBSERVER", uri)
    wait_task = asyncio.create_task(
        cli_messaging._wait(
            uri=uri,
            name="X-rx",
            for_name="X",
            timeout=0.0,
            poll_interval=0.01,
        )
    )
    await _wait_for_presence(observer, "X-rx")
    await close_agents(observer)
    await manager.__aexit__(None, None, None)
    code = await wait_task
    assert code == 3


async def test_wait_jitters_on_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[float, float]] = []

    def _rec(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    monkeypatch.setattr("synapse_channel.cli_messaging.random.uniform", _rec)
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                directed_only=True,
                wake_jitter=5.0,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "CEO", "all", "go")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0
    assert calls == [(0.0, 5.0)]  # jitter applied for the broadcast


async def test_wait_no_jitter_on_directed_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[float, float]] = []

    def _rec(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    monkeypatch.setattr("synapse_channel.cli_messaging.random.uniform", _rec)
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                wake_jitter=5.0,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "B", "hi")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0
    assert calls == []  # no jitter for a directed message
