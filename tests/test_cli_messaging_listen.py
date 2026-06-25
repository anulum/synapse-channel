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
from collections.abc import Coroutine
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_messaging
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub


async def _wait_for_presence(observer: AgentHandle, name: str) -> None:
    await observer.recorder.wait_for(
        lambda message: message.get("type") == "presence_update" and message.get("agent") == name
    )


async def test_listen_prints_chat_and_presence(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        listen_task = asyncio.create_task(
            cli_messaging._listen(uri=uri, name="USER", max_messages=3)
        )
        fast = await connect_agent("FAST", uri)
        try:
            await _wait_for_presence(observer, "USER")
            await fast.agent.chat("hi", target="all")
            await fast.agent.chat("again", target="all")
            code = await listen_task
        finally:
            await close_agents(fast, observer)

    assert code == 0
    out = capsys.readouterr().out
    assert "FAST: hi" in out
    assert "[presence] joined" in out


def test_cmd_listen_dispatches_real_command(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="USER",
        token=None,
        for_name=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_listen(ns) == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_listen_threads_token_to_agent() -> None:
    token = "s3cret"
    async with running_hub(SynapseHub(authenticator=TokenAuthenticator([token]))) as (_hub, uri):
        code = await cli_messaging._listen(
            uri=uri,
            name="U",
            token=token,
            max_messages=0,
        )

    assert code == 0


async def test_listen_for_filters_to_inbox(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        listen_task = asyncio.create_task(
            cli_messaging._listen(uri=uri, name="B-listener", for_name="B", max_messages=2)
        )
        sender = await connect_agent("A", uri)
        try:
            await _wait_for_presence(observer, "B-listener")
            await sender.agent.chat("just C", target="C")
            await sender.agent.chat("everyone", target="all")
            await sender.agent.chat("you two", target="B,C")
            code = await listen_task
        finally:
            await close_agents(sender, observer)

    assert code == 0
    out = capsys.readouterr().out
    assert "everyone" in out
    assert "you two" in out
    assert "just C" not in out
    assert "presence" not in out


def test_cmd_listen_handles_keyboard_interrupt(capsys: pytest.CaptureFixture[str]) -> None:
    def stop(coro: Coroutine[Any, Any, int]) -> int:
        coro.close()
        raise KeyboardInterrupt

    async def listen_once(**_: Any) -> int:
        return 0

    ns = argparse.Namespace(
        uri="ws://127.0.0.1:1",
        name="USER",
        token=None,
        for_name=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_listen(ns, listen_runner=listen_once, async_runner=stop) == 0
    assert "[USER] stopped listening." in capsys.readouterr().out
