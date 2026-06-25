# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only hub query commands (who/state/board/manifest/health)

from __future__ import annotations

import argparse
from typing import Any

import pytest

from cli_queries_helpers import FakeAgent, _factory
from synapse_channel import cli_queries


async def test_who_lists_project_agents(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snap: dict[str, Any] = {
        "type": "who_snapshot",
        "online_agents": ["quantum/agent-1", "quantum/agent-2", "other/agent-3"],
    }
    # The leading non-snapshot message exercises the collect() filter's reject path.
    factory = _factory(holder, inbound=[{"type": "chat", "payload": "noise"}, snap])
    code = await cli_queries._who(uri="ws://h", name="U", project="quantum", agent_factory=factory)
    assert code == 0
    out = capsys.readouterr().out
    assert "Online in quantum (2)" in out
    assert "quantum/agent-1" in out
    assert "other/agent-3" not in out


async def test_who_lists_all_without_project(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snap: dict[str, Any] = {"type": "who_snapshot", "online_agents": ["a", "b"]}
    factory = _factory(holder, inbound=[snap])
    code = await cli_queries._who(uri="ws://h", name="U", agent_factory=factory)
    assert code == 0
    assert "Online (2)" in capsys.readouterr().out


async def test_who_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli_queries._who(uri="ws://h", name="U", agent_factory=factory)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_who_returns_quietly_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(
        holder, inbound=[{"type": "chat", "sender": "X", "payload": "noise"}], idle=False
    )
    code = await cli_queries._who(uri="ws://h", name="U", agent_factory=factory)
    assert code == 0
    assert "Online" not in capsys.readouterr().out


def test_cmd_who_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="U", project=None, token=None)
    assert cli_queries._cmd_who(ns) == 0
