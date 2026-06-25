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


def test_print_manifest_renders_cards(capsys: pytest.CaptureFixture[str]) -> None:
    manifest = [
        {"agent": "FAST", "task_classes": ["chat"], "model": "m", "description": "quick"},
        {"agent": "BARE", "task_classes": [], "model": "", "description": ""},
    ]
    cli_queries._print_manifest(manifest)
    out = capsys.readouterr().out
    assert "FAST [chat] model=m: quick" in out
    assert "BARE [none] model=-:" in out


async def test_manifest_prints_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snapshot: dict[str, Any] = {
        "type": "manifest_snapshot",
        "manifest": [{"agent": "FAST", "task_classes": ["chat"], "model": "m", "description": "q"}],
    }
    noise: dict[str, Any] = {"type": "chat", "sender": "X", "payload": "hi"}
    factory = _factory(holder, inbound=[noise, snapshot])
    code = await cli_queries._manifest(uri="ws://h", name="USER", agent_factory=factory, token="t")
    assert code == 0
    assert holder[0].token == "t"
    assert "FAST [chat] model=m: q" in capsys.readouterr().out


async def test_manifest_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli_queries._manifest(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_manifest_returns_quietly_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(
        holder, inbound=[{"type": "chat", "sender": "X", "payload": "noise"}], idle=False
    )
    code = await cli_queries._manifest(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 0
    assert "Agents" not in capsys.readouterr().out


def test_cmd_manifest_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="USER", token=None)
    assert cli_queries._cmd_manifest(ns) == 0
