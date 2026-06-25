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


async def test_state_prints_claims_filtered(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snap: dict[str, Any] = {
        "type": "state_snapshot",
        "snapshot": {
            "active_claims": [
                {
                    "task_id": "T1",
                    "status": "working",
                    "owner": "quantum/agent-1",
                    "paths": ["src"],
                    "checkpoint": "cp1",
                },
                {
                    "task_id": "T2",
                    "status": "claimed",
                    "owner": "other/agent-2",
                    "paths": [],
                    "checkpoint": "",
                },
            ]
        },
    }
    factory = _factory(holder, inbound=[{"type": "chat", "payload": "noise"}, snap])
    code = await cli_queries._state(uri="ws://h", name="U", owner="quantum", agent_factory=factory)
    assert code == 0
    out = capsys.readouterr().out
    assert "Active claims (1)" in out
    assert "T1" in out
    assert "checkpoint=cp1" in out
    assert "other/agent-2" not in out


async def test_state_lists_all_without_owner(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snap: dict[str, Any] = {
        "type": "state_snapshot",
        "snapshot": {"active_claims": [{"task_id": "T1", "status": "working", "owner": "a"}]},
    }
    factory = _factory(holder, inbound=[snap])
    assert await cli_queries._state(uri="ws://h", name="U", agent_factory=factory) == 0
    assert "Active claims (1)" in capsys.readouterr().out


async def test_state_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    assert await cli_queries._state(uri="ws://h", name="U", agent_factory=factory) == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_state_quiet_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(holder, inbound=[{"type": "chat", "payload": "x"}], idle=False)
    assert await cli_queries._state(uri="ws://h", name="U", agent_factory=factory) == 0
    assert "Active claims" not in capsys.readouterr().out


def test_cmd_state_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="U", owner=None, token=None)
    assert cli_queries._cmd_state(ns) == 0


async def test_state_shows_git_branch(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snap: dict[str, Any] = {
        "type": "state_snapshot",
        "snapshot": {
            "active_claims": [
                {
                    "task_id": "T1",
                    "status": "working",
                    "owner": "a",
                    "paths": ["src"],
                    "checkpoint": "",
                    "git": {"branch": "feature/x", "base": "main", "auto_release_on": "merge"},
                }
            ]
        },
    }
    factory = _factory(holder, inbound=[snap])
    assert await cli_queries._state(uri="ws://h", name="U", agent_factory=factory) == 0
    assert "git=feature/x->main" in capsys.readouterr().out
