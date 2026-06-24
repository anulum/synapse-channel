# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only hub query commands (who/state/board/manifest/health)

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from synapse_channel import cli, cli_queries


class FakeAgent:
    """Stand-in for SynapseAgent used by the who/state/board/manifest/health flow tests."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
        ready: bool = True,
        inbound: list[dict[str, Any]] | None = None,
        idle: bool = True,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self._ready = ready
        self._inbound = inbound or []
        self._idle = idle

    async def connect(self) -> None:
        for message in self._inbound:
            await self.callback(message)
        if self._idle:
            await asyncio.Event().wait()  # block until cancelled

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def request_who(self) -> None:
        return None

    async def request_state(self) -> None:
        return None

    async def request_board(self) -> None:
        return None

    async def request_manifest(self) -> None:
        return None


def _factory(
    holder: list[FakeAgent],
    *,
    ready: bool = True,
    inbound: list[dict[str, Any]] | None = None,
    idle: bool = True,
) -> Callable[..., Any]:
    def make(
        name: str,
        callback: Any,
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
    ) -> Any:
        agent = FakeAgent(
            name,
            callback,
            uri=uri,
            verbose=verbose,
            token=token,
            ready=ready,
            inbound=inbound,
            idle=idle,
        )
        holder.append(agent)
        return agent

    return make


# --- parser ------------------------------------------------------------------


def test_parser_who() -> None:
    args = cli.build_parser().parse_args(["who", "--project", "quantum"])
    assert args.project == "quantum"
    assert args.func is cli_queries._cmd_who


def test_parser_state() -> None:
    args = cli.build_parser().parse_args(["state", "--owner", "quantum"])
    assert args.owner == "quantum"
    assert args.func is cli_queries._cmd_state


def test_parser_board() -> None:
    args = cli.build_parser().parse_args(["board", "--name", "WATCH"])
    assert args.name == "WATCH"
    assert args.func is cli_queries._cmd_board


def test_parser_manifest() -> None:
    manifest = cli.build_parser().parse_args(["manifest", "--name", "WATCH"])
    assert manifest.name == "WATCH"
    assert manifest.func is cli_queries._cmd_manifest


def test_parser_health() -> None:
    args = cli.build_parser().parse_args(["health", "--uri", "ws://x"])
    assert args.func is cli_queries._cmd_health
    assert args.uri == "ws://x"


# --- board -------------------------------------------------------------------


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


# --- capability manifest -----------------------------------------------------


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


# --- who (directory) ---------------------------------------------------------


async def test_who_lists_project_agents(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snap: dict[str, Any] = {
        "type": "who_snapshot",
        "online_agents": ["quantum/claude-1", "quantum/codex-2", "other/gemini-3"],
    }
    # The leading non-snapshot message exercises the collect() filter's reject path.
    factory = _factory(holder, inbound=[{"type": "chat", "payload": "noise"}, snap])
    code = await cli_queries._who(uri="ws://h", name="U", project="quantum", agent_factory=factory)
    assert code == 0
    out = capsys.readouterr().out
    assert "Online in quantum (2)" in out
    assert "quantum/claude-1" in out
    assert "other/gemini-3" not in out


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


# --- state (recovery) --------------------------------------------------------


async def test_state_prints_claims_filtered(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snap: dict[str, Any] = {
        "type": "state_snapshot",
        "snapshot": {
            "active_claims": [
                {
                    "task_id": "T1",
                    "status": "working",
                    "owner": "quantum/claude-1",
                    "paths": ["src"],
                    "checkpoint": "cp1",
                },
                {
                    "task_id": "T2",
                    "status": "claimed",
                    "owner": "other/codex-2",
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
    assert "other/codex-2" not in out


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


# --- health probe ------------------------------------------------------------


async def test_health_ok_when_ready() -> None:
    holder: list[FakeAgent] = []
    code = await cli_queries._health(
        uri="ws://h", name="H", agent_factory=_factory(holder, ready=True)
    )
    assert code == 0


async def test_health_fail_when_unreachable() -> None:
    holder: list[FakeAgent] = []
    code = await cli_queries._health(
        uri="ws://h", name="H", agent_factory=_factory(holder, ready=False)
    )
    assert code == 1


async def test_drop_message_is_noop() -> None:
    await cli_queries._drop_message({"type": "x"})  # a no-op callback; must simply not raise


def test_cmd_health_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli_queries, "_health", fake)
    assert cli_queries._cmd_health(argparse.Namespace(uri="ws://h", name="H", token=None)) == 0
