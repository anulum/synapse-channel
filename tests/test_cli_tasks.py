# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the shared task-plan write commands (task declare/update/progress)

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from synapse_channel import cli, cli_tasks


class FakeAgent:
    """Stand-in for SynapseAgent used by the task declare/update/progress flow tests."""

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
        self.posted_tasks: list[tuple[str, str, tuple[str, ...]]] = []
        self.ledger_updates: list[tuple[str, str | None]] = []
        self.progress_posts: list[tuple[str, str, str]] = []
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

    async def post_task(
        self,
        task_id: str,
        *,
        title: str = "",
        depends_on: Any = (),
        suggested_owner: str = "",
    ) -> None:
        self.posted_tasks.append((task_id, title, tuple(depends_on)))

    async def update_ledger_task(
        self, task_id: str, *, status: str | None = None, suggested_owner: str | None = None
    ) -> None:
        self.ledger_updates.append((task_id, status))

    async def post_progress(self, task_id: str, text: str, *, kind: str = "note") -> None:
        self.progress_posts.append((task_id, text, kind))


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


def test_parser_task_declare() -> None:
    args = cli.build_parser().parse_args(
        ["task", "declare", "BUILD", "--title", "Compile", "--depends-on", "X"]
    )
    assert args.task_id == "BUILD"
    assert args.title == "Compile"
    assert args.depends_on == ["X"]
    assert args.func is cli_tasks._cmd_task_declare


def test_parser_task_update_and_progress() -> None:
    upd = cli.build_parser().parse_args(["task", "update", "BUILD", "--status", "done"])
    assert upd.task_id == "BUILD"
    assert upd.status == "done"
    assert upd.func is cli_tasks._cmd_task_update
    prog = cli.build_parser().parse_args(["task", "progress", "T", "running", "--kind", "blocker"])
    assert prog.text == "running"
    assert prog.kind == "blocker"
    assert prog.func is cli_tasks._cmd_task_progress


def test_task_bare_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.build_parser().parse_args(["task"])
    assert args.func is cli_tasks._cmd_task_help
    assert cli_tasks._cmd_task_help(args) == 1
    assert "synapse task" in capsys.readouterr().out


# --- declare / update / progress ---------------------------------------------


def test_cmd_task_declare_prints_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    confirm = {
        "type": "ledger_task_posted",
        "task": {"task_id": "BUILD", "title": "Compile", "depends_on": ["X"]},
    }
    # The leading non-matching message exercises the collect() filter's reject path.
    factory = _factory(holder, inbound=[{"type": "chat", "payload": "noise"}, confirm])
    ns = argparse.Namespace(
        task_id="BUILD", title="Compile", depends_on=["X"], uri="ws://h", name="P", token=None
    )
    assert cli_tasks._cmd_task_declare(ns, agent_factory=factory) == 0
    out = capsys.readouterr().out
    assert "declared BUILD" in out
    assert "deps: X" in out
    assert holder[0].posted_tasks == [("BUILD", "Compile", ("X",))]


def test_cmd_task_update_prints_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    confirm = {"type": "ledger_task_updated", "task": {"task_id": "BUILD", "status": "done"}}
    factory = _factory(holder, inbound=[confirm])
    ns = argparse.Namespace(
        task_id="BUILD", status="done", suggested_owner=None, uri="ws://h", name="P", token=None
    )
    assert cli_tasks._cmd_task_update(ns, agent_factory=factory) == 0
    assert "status=done" in capsys.readouterr().out
    assert holder[0].ledger_updates == [("BUILD", "done")]


def test_cmd_task_progress_prints_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    confirm = {
        "type": "ledger_progress_posted",
        "progress": {"task_id": "TEST", "kind": "note", "text": "go"},
    }
    factory = _factory(holder, inbound=[confirm])
    ns = argparse.Namespace(
        task_id="TEST", text="go", kind="note", uri="ws://h", name="P", token=None
    )
    assert cli_tasks._cmd_task_progress(ns, agent_factory=factory) == 0
    assert "posted note on TEST: go" in capsys.readouterr().out
    assert holder[0].progress_posts == [("TEST", "go", "note")]


async def test_task_action_returns_one_when_hub_unreachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)

    async def send(agent: Any) -> None:
        return None

    code = await cli_tasks._task_action(
        uri="ws://h",
        name="P",
        token=None,
        confirm_type="x",
        send=send,
        render=lambda m: "",
        agent_factory=factory,
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_task_action_returns_quietly_when_no_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    # The poll loop that sleeps lives in cli_queries._query_hub, which _task_action reuses.
    monkeypatch.setattr("synapse_channel.cli_queries.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(
        holder, inbound=[{"type": "chat", "sender": "X", "payload": "noise"}], idle=False
    )

    async def send(agent: Any) -> None:
        return None

    code = await cli_tasks._task_action(
        uri="ws://h",
        name="P",
        token=None,
        confirm_type="ledger_task_posted",
        send=send,
        render=lambda m: "SHOULD-NOT-PRINT",
        agent_factory=factory,
    )
    assert code == 0
    assert "SHOULD-NOT-PRINT" not in capsys.readouterr().out
