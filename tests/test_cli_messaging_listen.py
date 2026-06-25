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


async def test_listen_prints_chat_and_presence(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "FAST", "payload": "hi"},
        {"type": "presence_update", "event": "joined", "online_agents": ["FAST", "USER"]},
        {"type": "welcome"},  # ignored type
    ]
    factory = _factory(holder, inbound=inbound, idle=False)
    code = await cli_messaging._listen(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 0
    out = capsys.readouterr().out
    assert "FAST: hi" in out
    assert "[presence] joined -> online: FAST, USER" in out


def test_cmd_listen_dispatch_and_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="USER", token=None, for_name=None)
    assert cli_messaging._cmd_listen(ns) == 0

    def interrupt(coro: Any) -> int:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", interrupt)
    assert cli_messaging._cmd_listen(ns) == 0


async def test_listen_threads_token_to_agent() -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, inbound=[], idle=False)
    await cli_messaging._listen(uri="ws://h", name="U", agent_factory=factory, token="s3cret")
    assert holder[0].token == "s3cret"


async def test_listen_for_filters_to_inbox(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "all", "payload": "everyone"},
        {"type": "chat", "sender": "A", "target": "B,C", "payload": "you two"},
        {"type": "chat", "sender": "A", "target": "C", "payload": "just C"},
        {"type": "presence_update", "event": "joined", "online_agents": ["B"]},
    ]
    factory = _factory(holder, inbound=inbound, idle=False)
    code = await cli_messaging._listen(uri="ws://h", name="B", agent_factory=factory, for_name="B")
    assert code == 0
    out = capsys.readouterr().out
    assert "everyone" in out
    assert "you two" in out
    assert "just C" not in out
    assert "presence" not in out
