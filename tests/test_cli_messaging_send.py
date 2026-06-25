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


async def test_send_delivers_message_and_prints_replies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "FAST", "payload": "pong"},
        {"type": "chat", "sender": "USER", "payload": "own-echo"},  # filtered: self
        {"type": "welcome"},  # filtered: not a chat
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._send(
        uri="ws://h",
        name="USER",
        target="FAST",
        message="ping",
        wait_seconds=0.01,
        agent_factory=factory,
    )
    assert code == 0
    assert holder[0].chats == [("FAST", "ping")]
    out = capsys.readouterr().out
    assert "FAST: pong" in out
    assert "own-echo" not in out


async def test_send_waits_but_prints_nothing_without_replies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, inbound=[])
    code = await cli_messaging._send(
        uri="ws://h",
        name="USER",
        target="all",
        message="ping",
        wait_seconds=0.01,
        agent_factory=factory,
    )
    assert code == 0
    assert capsys.readouterr().out == ""


async def test_send_skips_wait_when_zero() -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder)
    code = await cli_messaging._send(
        uri="ws://h",
        name="USER",
        target="all",
        message="ping",
        wait_seconds=0.0,
        agent_factory=factory,
    )
    assert code == 0
    assert holder[0].chats == [("all", "ping")]


async def test_send_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli_messaging._send(
        uri="ws://h",
        name="USER",
        target="all",
        message="ping",
        wait_seconds=0.0,
        agent_factory=factory,
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


def test_cmd_send_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="USER",
        target="all",
        message="hi",
        wait_seconds=0.0,
        priority=False,
        token=None,
    )
    assert cli_messaging._cmd_send(ns) == 0


async def test_send_threads_token_to_agent() -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder)
    await cli_messaging._send(
        uri="ws://h",
        name="U",
        target="all",
        message="hi",
        wait_seconds=0.0,
        agent_factory=factory,
        token="s3cret",
    )
    assert holder[0].token == "s3cret"


async def test_send_marks_priority() -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, idle=False)
    code = await cli_messaging._send(
        uri="ws://h",
        name="U",
        target="all",
        message="!",
        wait_seconds=0.0,
        priority=True,
        agent_factory=factory,
    )
    assert code == 0
    assert holder[0].chats == [("all", "!")]
    assert holder[0].chat_priorities == [True]
