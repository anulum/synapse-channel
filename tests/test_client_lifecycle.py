# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-hub tests for the async client lifecycle

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
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


async def test_connect_accepts_custom_ping_keepalive_on_real_hub() -> None:
    async with running_hub() as (_, uri):
        handle = await connect_agent(
            "A",
            uri,
            wait_presence=False,
            takeover=False,
        )
        try:
            agent = SynapseAgent(
                "B",
                uri=uri,
                ping_interval=7.0,
                ping_timeout=9.0,
                verbose=False,
            )
            task = asyncio.create_task(agent.connect())
            try:
                assert await agent.wait_until_ready(timeout=1.0) is True
                assert agent.ping_interval == 7.0
                assert agent.ping_timeout == 9.0
            finally:
                agent.running = False
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        finally:
            await close_agents(handle)


async def test_connect_registers_dispatches_and_filters_echo() -> None:
    received: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        received.append(data)

    async with running_hub() as (_, uri):
        agent_a = SynapseAgent("A", callback, uri=uri, heartbeat_interval=60.0, verbose=True)
        task_a = asyncio.create_task(agent_a.connect())
        try:
            assert await agent_a.wait_until_ready(timeout=1.0) is True
            handle_b = await connect_agent("B", uri, wait_presence=False)
            try:
                await agent_a.chat("mine", target="all")
                await handle_b.agent.chat("hi", target="A")
                for _ in range(50):
                    chat_payloads = [
                        message.get("payload")
                        for message in received
                        if message.get("type") == "chat"
                    ]
                    if "hi" in chat_payloads:
                        break
                    await asyncio.sleep(0.01)
                assert agent_a.hub_id != "unknown"
                assert agent_a.ready_event.is_set()
                assert "hi" in chat_payloads
                assert "mine" not in chat_payloads
            finally:
                await close_agents(handle_b)
        finally:
            agent_a.running = False
            task_a.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task_a


async def test_dispatch_rejects_malformed_json_without_callback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    received: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        received.append(data)

    agent = SynapseAgent("A", callback, verbose=True)
    await agent._dispatch("this-is-not-json")

    assert received == []
    assert "malformed JSON" in capsys.readouterr().out


async def test_connect_without_callback_still_processes_welcome() -> None:
    async with running_hub() as (_, uri):
        agent = SynapseAgent("A", None, uri=uri, heartbeat_interval=60.0, verbose=False)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(timeout=1.0) is True
            assert agent.hub_id != "unknown"
        finally:
            agent.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def test_quiet_dispatch_skips_lifecycle_prints(capsys: pytest.CaptureFixture[str]) -> None:
    agent = SynapseAgent("A", verbose=False)
    await agent._dispatch(json.dumps({"type": "welcome", "hub_id": "h"}))
    await agent._dispatch("not-json")

    out = capsys.readouterr().out
    assert "connected to Synapse" not in out
    assert "malformed JSON" not in out
    assert agent.hub_id == "h"


async def test_connect_stops_when_running_cleared_by_callback() -> None:
    seen: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        seen.append(data)
        if data.get("type") == "chat":
            agent.running = False

    async with running_hub() as (_, uri):
        agent = SynapseAgent("A", callback, uri=uri, heartbeat_interval=60.0, verbose=False)
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(timeout=1.0) is True
            handle_b = await connect_agent("B", uri, wait_presence=False)
            try:
                await handle_b.agent.chat("1", target="A")
                await handle_b.agent.chat("2", target="A")
                for _ in range(50):
                    if not agent.running:
                        break
                    await asyncio.sleep(0.01)
            finally:
                await close_agents(handle_b)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert any(message.get("payload") == "1" for message in seen)


async def test_connect_handles_connection_refused(capsys: pytest.CaptureFixture[str]) -> None:
    agent = SynapseAgent("A", uri=f"ws://localhost:{_free_port()}", verbose=True)
    await agent.connect()
    assert "could not connect" in capsys.readouterr().out


async def test_connect_refused_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    agent = SynapseAgent("A", uri=f"ws://localhost:{_free_port()}", verbose=False)
    await agent.connect()
    assert capsys.readouterr().out == ""
