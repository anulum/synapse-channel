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


def test_print_board_renders_tasks_ready_and_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    board = {
        "tasks": [
            {"status": "open", "task_id": "A", "title": "Alpha", "depends_on": []},
            {"status": "blocked", "task_id": "B", "title": "Beta", "depends_on": ["A"]},
        ],
        "ready": ["A"],
        "progress": [{"author": "FAST", "kind": "note", "task_id": "A", "text": "go"}],
    }
    cli_queries._print_board(board)
    out = capsys.readouterr().out
    assert "[open] A — Alpha" in out
    assert "[blocked] B — Beta  (deps: A)" in out
    assert "Ready: A" in out
    assert "FAST [note] A: go" in out


def test_print_board_empty_ready_and_no_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_queries._print_board({"tasks": [], "ready": [], "progress": []})
    out = capsys.readouterr().out
    assert "Ready: (none)" in out
    assert "Recent progress" not in out


def test_print_board_progress_note_without_task(
    capsys: pytest.CaptureFixture[str],
) -> None:
    note = {"author": "P", "kind": "assessment", "text": "ok"}
    cli_queries._print_board({"tasks": [], "ready": [], "progress": [note]})
    assert "P [assessment] -: ok" in capsys.readouterr().out


async def test_board_prints_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snapshot: dict[str, Any] = {
        "type": "board_snapshot",
        "board": {
            "tasks": [{"status": "open", "task_id": "A", "title": "Alpha", "depends_on": []}],
            "ready": ["A"],
            "progress": [],
        },
    }
    # A non-board message first exercises the snapshot filter's negative path.
    noise: dict[str, Any] = {"type": "chat", "sender": "X", "payload": "hi"}
    factory = _factory(holder, inbound=[noise, snapshot])
    code = await cli_queries._board(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 0
    assert "[open] A — Alpha" in capsys.readouterr().out


async def test_board_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli_queries._board(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_board_returns_quietly_when_no_snapshot_arrives(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(
        holder, inbound=[{"type": "chat", "sender": "X", "payload": "noise"}], idle=False
    )
    code = await cli_queries._board(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 0
    assert "Tasks" not in capsys.readouterr().out


def test_cmd_board_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="USER", token=None)
    assert cli_queries._cmd_board(ns) == 0


async def test_board_threads_token_to_agent() -> None:
    holder: list[FakeAgent] = []
    snapshot: dict[str, Any] = {
        "type": "board_snapshot",
        "board": {"tasks": [], "ready": [], "progress": []},
    }
    factory = _factory(holder, inbound=[snapshot])
    await cli_queries._board(uri="ws://h", name="U", agent_factory=factory, token="s3cret")
    assert holder[0].token == "s3cret"
