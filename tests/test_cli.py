# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the unified command-line entry point

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.client.llm_worker import DEFAULT_OLLAMA_BASE_URL
from synapse_channel.core.hub import SynapseHub
from synapse_channel.git.gitclaim import GitError


class FakeAgent:
    """Configurable stand-in for SynapseAgent used by send/listen tests."""

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
        self.chats: list[tuple[str, str]] = []
        self.posted_tasks: list[tuple[str, str, tuple[str, ...]]] = []
        self.ledger_updates: list[tuple[str, str | None]] = []
        self.progress_posts: list[tuple[str, str, str]] = []
        self.claims: list[str] = []
        self.claim_worktrees: list[str] = []
        self.releases: list[str] = []
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

    async def chat(self, payload: str, *, target: str = "all", priority: bool = False) -> None:
        self.chats.append((target, payload))
        self.chat_priorities: list[bool] = getattr(self, "chat_priorities", [])
        self.chat_priorities.append(priority)

    async def request_board(self) -> None:
        self.board_requests = getattr(self, "board_requests", 0) + 1

    async def request_manifest(self) -> None:
        self.manifest_requests = getattr(self, "manifest_requests", 0) + 1

    async def request_who(self) -> None:
        self.who_requests = getattr(self, "who_requests", 0) + 1

    async def request_state(self) -> None:
        self.state_requests = getattr(self, "state_requests", 0) + 1

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

    async def claim(
        self,
        task_id: str,
        *,
        note: str = "",
        ttl_seconds: float | None = None,
        worktree: str = "",
        paths: Any = (),
        idem_key: str | None = None,
    ) -> None:
        self.claims.append(task_id)
        self.claim_worktrees.append(worktree)

    async def release(self, task_id: str, *, idem_key: str | None = None) -> None:
        self.releases.append(task_id)


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
        takeover: bool = False,
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


def test_parser_hub_defaults() -> None:
    args = cli.build_parser().parse_args(["hub"])
    assert args.host == "localhost"
    assert args.db is None
    assert args.func is cli._cmd_hub


def test_parser_worker_custom() -> None:
    args = cli.build_parser().parse_args(
        ["worker", "--name", "REASON", "--provider", "rule", "--min-reply-interval", "1.5"]
    )
    assert args.name == "REASON"
    assert args.provider == "rule"
    assert args.min_reply_interval == 1.5


def test_parser_send_and_listen() -> None:
    send = cli.build_parser().parse_args(
        ["send", "hello", "--target", "FAST", "--wait-seconds", "0"]
    )
    assert send.message == "hello"
    assert send.target == "FAST"
    assert send.wait_seconds == 0.0

    listen = cli.build_parser().parse_args(["listen", "--name", "WATCH"])
    assert listen.name == "WATCH"


# --- main dispatch -----------------------------------------------------------


def test_run_executes_coroutine() -> None:
    marker: list[bool] = []

    async def noop() -> None:
        marker.append(True)

    cli._run(noop())
    assert marker == [True]


def test_main_without_command_prints_help() -> None:
    assert cli.main([]) == 1


def test_main_version_exits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "update_notice", lambda: None)  # no network in tests
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "synapse-channel" in capsys.readouterr().out


def test_main_version_prints_update_notice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "update_notice", lambda: "  → 9.9.9 is available")
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    captured = capsys.readouterr()
    assert "synapse-channel" in captured.out
    assert "9.9.9 is available" in captured.err  # the notice goes to stderr


def test_main_routes_to_team(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_team", lambda **kwargs: 9)
    assert cli.main(["team", "--no-workers"]) == 9


def test_main_routes_to_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    assert cli.main(["hub", "--port", "9000"]) == 0


# --- hub / worker handlers ---------------------------------------------------


def _hub_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "host": "localhost",
        "port": 8876,
        "db": None,
        "rate": 0.0,
        "burst": 20.0,
        "max_history": 10000,
        "relay_log": None,
        "relay_max_lines": 5000,
        "max_clients": 64,
        "max_msg_kb": 1024,
        "token": None,
        "metrics": False,
        "auth_timeout": 10.0,
        "metrics_token": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_hub_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    ns = _hub_ns()
    assert cli._cmd_hub(ns) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run", interrupt)
    assert cli._cmd_hub(ns) == 0


def test_cmd_hub_with_db_opens_and_closes_event_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    db = tmp_path / "events.db"
    assert cli._cmd_hub(_hub_ns(db=str(db))) == 0
    # The persistent store was created (and closed) for the run.
    assert db.exists()


def test_cmd_hub_with_rate_limit_builds_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(coro: Any) -> None:
        coro.close()

    monkeypatch.setattr(cli, "_run", fake_run)

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli.SynapseHub", spy_hub)
    assert cli._cmd_hub(_hub_ns(rate=5.0, burst=10.0)) == 0
    assert captured["rate_limiter"] is not None


def _worker_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "name": "FAST",
        "prefix": "",
        "uri": DEFAULT_HUB_URI,
        "provider": "rule",
        "model": "llama3",
        "base_url": DEFAULT_OLLAMA_BASE_URL,
        "api_key_env": "OPENAI_API_KEY",
        "max_context": 8,
        "reply_target_mode": "all",
        "min_reply_interval": 0.7,
        "token": None,
        "task_class": None,
        "heavy_model": "",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_worker_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    assert cli._cmd_worker(_worker_ns()) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run", interrupt)
    assert cli._cmd_worker(_worker_ns()) == 0


def test_cmd_team_returns_runner_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_team", lambda **kwargs: 4)
    ns = argparse.Namespace(
        port=8876, no_workers=False, fast_model=None, reason_model=None, prefix=""
    )
    assert cli._cmd_team(ns) == 4


def test_cmd_worker_applies_name_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class _StubWorker:
        def __init__(self, *, name: str, **_: Any) -> None:
            captured["name"] = name

        async def run(self) -> None:
            return None

    monkeypatch.setattr(cli, "SynapseLLMWorker", _StubWorker)
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    assert cli._cmd_worker(_worker_ns(prefix="remanentia/", name="FAST")) == 0
    assert captured["name"] == "remanentia/FAST"


def test_cmd_team_threads_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "run_team", lambda **kwargs: captured.update(kwargs) or 0)
    ns = argparse.Namespace(
        port=8876, no_workers=False, fast_model=None, reason_model=None, prefix="proj/"
    )
    assert cli._cmd_team(ns) == 0
    assert captured["prefix"] == "proj/"


# --- send --------------------------------------------------------------------


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
    code = await cli._send(
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
    code = await cli._send(
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
    code = await cli._send(
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
    code = await cli._send(
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
    monkeypatch.setattr("synapse_channel.cli.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="USER",
        target="all",
        message="hi",
        wait_seconds=0.0,
        priority=False,
        token=None,
    )
    assert cli._cmd_send(ns) == 0


# --- listen ------------------------------------------------------------------


async def test_listen_prints_chat_and_presence(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "FAST", "payload": "hi"},
        {"type": "presence_update", "event": "joined", "online_agents": ["FAST", "USER"]},
        {"type": "welcome"},  # ignored type
    ]
    factory = _factory(holder, inbound=inbound, idle=False)
    code = await cli._listen(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 0
    out = capsys.readouterr().out
    assert "FAST: hi" in out
    assert "[presence] joined -> online: FAST, USER" in out


def test_cmd_listen_dispatch_and_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="USER", token=None, for_name=None)
    assert cli._cmd_listen(ns) == 0

    def interrupt(coro: Any) -> int:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.cli.asyncio.run", interrupt)
    assert cli._cmd_listen(ns) == 0


# --- hub relay-log wiring ----------------------------------------------------


def test_cmd_hub_wires_relay_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli.SynapseHub", spy_hub)
    log = tmp_path / "relay.ndjson"
    assert cli._cmd_hub(_hub_ns(relay_log=str(log), relay_max_lines=42)) == 0
    assert captured["relay_log"] == str(log)
    assert captured["relay_max_lines"] == 42


# --- git-release UX ----------------------------------------------------------


def test_parser_git_release_trigger_is_optional() -> None:
    args = cli.build_parser().parse_args(["git-release"])
    assert args.task_id is None
    assert args.trigger is None
    assert args.func is cli._cmd_git_release


def test_cmd_git_release_positional_redirects_to_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = argparse.Namespace(
        task_id="studio-panel", trigger=None, uri="ws://h", name="ME", token=None
    )
    assert cli._cmd_git_release(ns) == 2
    err = capsys.readouterr().err
    assert "synapse release studio-panel --name ME" in err  # the verb they actually wanted


def test_cmd_git_release_missing_trigger_explains(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(task_id=None, trigger=None, uri="ws://h", name="ME", token=None)
    assert cli._cmd_git_release(ns) == 2
    assert "--trigger" in capsys.readouterr().err


# --- board -------------------------------------------------------------------


def test_parser_board() -> None:
    args = cli.build_parser().parse_args(["board", "--name", "WATCH"])
    assert args.name == "WATCH"
    assert args.func is cli._cmd_board


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
    cli._print_board(board)
    out = capsys.readouterr().out
    assert "[open] A — Alpha" in out
    assert "[blocked] B — Beta  (deps: A)" in out
    assert "Ready: A" in out
    assert "FAST [note] A: go" in out


def test_print_board_empty_ready_and_no_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._print_board({"tasks": [], "ready": [], "progress": []})
    out = capsys.readouterr().out
    assert "Ready: (none)" in out
    assert "Recent progress" not in out


def test_print_board_progress_note_without_task(
    capsys: pytest.CaptureFixture[str],
) -> None:
    note = {"author": "P", "kind": "assessment", "text": "ok"}
    cli._print_board({"tasks": [], "ready": [], "progress": [note]})
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
    code = await cli._board(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 0
    assert "[open] A — Alpha" in capsys.readouterr().out


async def test_board_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli._board(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_board_returns_quietly_when_no_snapshot_arrives(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.cli.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(
        holder, inbound=[{"type": "chat", "sender": "X", "payload": "noise"}], idle=False
    )
    code = await cli._board(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 0
    assert "Tasks" not in capsys.readouterr().out


def test_cmd_board_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="USER", token=None)
    assert cli._cmd_board(ns) == 0


# --- connect authentication threading ----------------------------------------


def test_parser_token_options() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["hub", "--token", "h"]).token == "h"
    assert parser.parse_args(["worker", "--token", "w"]).token == "w"
    assert parser.parse_args(["send", "msg", "--token", "s"]).token == "s"
    assert parser.parse_args(["listen", "--token", "l"]).token == "l"
    assert parser.parse_args(["board", "--token", "b"]).token == "b"


def test_cmd_hub_with_token_builds_authenticator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli.SynapseHub", spy_hub)
    assert cli._cmd_hub(_hub_ns(token="s3cret")) == 0
    assert captured["authenticator"] is not None


def test_cmd_hub_without_token_has_no_authenticator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    def spy_hub(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    monkeypatch.setattr("synapse_channel.cli.SynapseHub", spy_hub)
    assert cli._cmd_hub(_hub_ns()) == 0
    assert captured["authenticator"] is None


def test_cmd_worker_threads_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    class FakeWorker:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr("synapse_channel.cli.SynapseLLMWorker", FakeWorker)
    assert cli._cmd_worker(_worker_ns(token="w0rk")) == 0
    assert captured["token"] == "w0rk"


async def test_send_threads_token_to_agent() -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder)
    await cli._send(
        uri="ws://h",
        name="U",
        target="all",
        message="hi",
        wait_seconds=0.0,
        agent_factory=factory,
        token="s3cret",
    )
    assert holder[0].token == "s3cret"


async def test_listen_threads_token_to_agent() -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, inbound=[], idle=False)
    await cli._listen(uri="ws://h", name="U", agent_factory=factory, token="s3cret")
    assert holder[0].token == "s3cret"


async def test_board_threads_token_to_agent() -> None:
    holder: list[FakeAgent] = []
    snapshot: dict[str, Any] = {
        "type": "board_snapshot",
        "board": {"tasks": [], "ready": [], "progress": []},
    }
    factory = _factory(holder, inbound=[snapshot])
    await cli._board(uri="ws://h", name="U", agent_factory=factory, token="s3cret")
    assert holder[0].token == "s3cret"


# --- supervisor --------------------------------------------------------------


def test_parser_supervisor() -> None:
    args = cli.build_parser().parse_args(["supervisor", "--idle-seconds", "60", "--interval", "5"])
    assert args.idle_seconds == 60.0
    assert args.interval == 5.0
    assert args.func is cli._cmd_supervisor


def test_cmd_supervisor_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    ns = argparse.Namespace(
        uri="ws://h", name="SUPERVISOR", idle_seconds=300.0, interval=30.0, token=None
    )
    assert cli._cmd_supervisor(ns) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run", interrupt)
    assert cli._cmd_supervisor(ns) == 0


# --- capability manifest -----------------------------------------------------


def test_parser_manifest_and_worker_task_class() -> None:
    parser = cli.build_parser()
    manifest = parser.parse_args(["manifest", "--name", "WATCH"])
    assert manifest.name == "WATCH"
    assert manifest.func is cli._cmd_manifest
    worker = parser.parse_args(["worker", "--task-class", "reason", "--task-class", "heavy"])
    assert worker.task_class == ["reason", "heavy"]


def test_cmd_worker_threads_task_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())

    class FakeWorker:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr("synapse_channel.cli.SynapseLLMWorker", FakeWorker)
    assert cli._cmd_worker(_worker_ns(task_class=["reason"], heavy_model="big")) == 0
    assert captured["task_classes"] == ("reason",)
    assert captured["heavy_model"] == "big"
    # Without --task-class the worker advertises the default class.
    captured.clear()
    assert cli._cmd_worker(_worker_ns()) == 0
    assert captured["task_classes"] == ("chat",)


def test_parser_worker_tiered_provider_and_heavy_model() -> None:
    args = cli.build_parser().parse_args(["worker", "--provider", "tiered", "--heavy-model", "big"])
    assert args.provider == "tiered"
    assert args.heavy_model == "big"


def test_print_manifest_renders_cards(capsys: pytest.CaptureFixture[str]) -> None:
    manifest = [
        {"agent": "FAST", "task_classes": ["chat"], "model": "m", "description": "quick"},
        {"agent": "BARE", "task_classes": [], "model": "", "description": ""},
    ]
    cli._print_manifest(manifest)
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
    code = await cli._manifest(uri="ws://h", name="USER", agent_factory=factory, token="t")
    assert code == 0
    assert holder[0].token == "t"
    assert "FAST [chat] model=m: q" in capsys.readouterr().out


async def test_manifest_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli._manifest(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_manifest_returns_quietly_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.cli.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(
        holder, inbound=[{"type": "chat", "sender": "X", "payload": "noise"}], idle=False
    )
    code = await cli._manifest(uri="ws://h", name="USER", agent_factory=factory)
    assert code == 0
    assert "Agents" not in capsys.readouterr().out


def test_cmd_manifest_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="USER", token=None)
    assert cli._cmd_manifest(ns) == 0


# --- task subcommand ---------------------------------------------------------


def test_parser_task_declare() -> None:
    args = cli.build_parser().parse_args(
        ["task", "declare", "BUILD", "--title", "Compile", "--depends-on", "X"]
    )
    assert args.task_id == "BUILD"
    assert args.title == "Compile"
    assert args.depends_on == ["X"]
    assert args.func is cli._cmd_task_declare


def test_parser_task_update_and_progress() -> None:
    upd = cli.build_parser().parse_args(["task", "update", "BUILD", "--status", "done"])
    assert upd.task_id == "BUILD"
    assert upd.status == "done"
    assert upd.func is cli._cmd_task_update
    prog = cli.build_parser().parse_args(["task", "progress", "T", "running", "--kind", "blocker"])
    assert prog.text == "running"
    assert prog.kind == "blocker"
    assert prog.func is cli._cmd_task_progress


def test_task_bare_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.build_parser().parse_args(["task"])
    assert args.func is cli._cmd_task_help
    assert cli._cmd_task_help(args) == 1
    assert "synapse task" in capsys.readouterr().out


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
    assert cli._cmd_task_declare(ns, agent_factory=factory) == 0
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
    assert cli._cmd_task_update(ns, agent_factory=factory) == 0
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
    assert cli._cmd_task_progress(ns, agent_factory=factory) == 0
    assert "posted note on TEST: go" in capsys.readouterr().out
    assert holder[0].progress_posts == [("TEST", "go", "note")]


async def test_task_action_returns_one_when_hub_unreachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)

    async def send(agent: Any) -> None:
        return None

    code = await cli._task_action(
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

    monkeypatch.setattr("synapse_channel.cli.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(
        holder, inbound=[{"type": "chat", "sender": "X", "payload": "noise"}], idle=False
    )

    async def send(agent: Any) -> None:
        return None

    code = await cli._task_action(
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


async def test_listen_for_filters_to_inbox(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "all", "payload": "everyone"},
        {"type": "chat", "sender": "A", "target": "B,C", "payload": "you two"},
        {"type": "chat", "sender": "A", "target": "C", "payload": "just C"},
        {"type": "presence_update", "event": "joined", "online_agents": ["B"]},
    ]
    factory = _factory(holder, inbound=inbound, idle=False)
    code = await cli._listen(uri="ws://h", name="B", agent_factory=factory, for_name="B")
    assert code == 0
    out = capsys.readouterr().out
    assert "everyone" in out
    assert "you two" in out
    assert "just C" not in out
    assert "presence" not in out


def test_parser_relay_and_listen_for_flag() -> None:
    relay = cli.build_parser().parse_args(["relay", "feed.ndjson", "--for", "B"])
    assert relay.for_name == "B"
    listen = cli.build_parser().parse_args(["listen", "--name", "B", "--for", "B"])
    assert listen.for_name == "B"


# --- wait (wake trigger) -----------------------------------------------------


def test_parser_wait() -> None:
    args = cli.build_parser().parse_args(["wait", "--name", "X", "--for", "Y", "--timeout", "5"])
    assert args.name == "X"
    assert args.for_name == "Y"
    assert args.timeout == 5.0
    assert args.func is cli._cmd_wait


async def test_wait_returns_on_addressed_message(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "presence_update", "sender": "hub"},  # not a chat — ignored
        {"type": "chat", "sender": "A", "target": "B", "payload": "wake up"},
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli._wait(
        uri="ws://h", name="B-rx", for_name="B", timeout=2.0, agent_factory=factory
    )
    assert code == 0
    assert "A: wake up" in capsys.readouterr().out


async def test_wait_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli._wait(uri="ws://h", name="B", for_name="B", timeout=1.0, agent_factory=factory)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_wait_times_out_with_nothing() -> None:
    holder: list[FakeAgent] = []
    # idle=True keeps the connection up so this exercises the timeout path (code 2),
    # distinct from a dropped connection (code 3).
    factory = _factory(holder, inbound=[], idle=True)
    code = await cli._wait(uri="ws://h", name="B", for_name="B", timeout=0.2, agent_factory=factory)
    assert code == 2


def test_cmd_wait_dispatches_with_for_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="X",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
    )
    assert cli._cmd_wait(ns) == 0


async def test_wait_ignores_own_messages() -> None:
    holder: list[FakeAgent] = []
    # A broadcast whose sender is our own identity (we send as for_name) must not wake us.
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "B", "target": "all", "payload": "x"}
    ]
    factory = _factory(holder, inbound=inbound, idle=True)
    code = await cli._wait(
        uri="ws://h", name="B-rx", for_name="B", timeout=0.2, agent_factory=factory
    )
    assert code == 2


def test_parser_wait_directed_only() -> None:
    args = cli.build_parser().parse_args(["wait", "--for", "B", "--directed-only"])
    assert args.directed_only is True


async def test_wait_directed_only_ignores_broadcast() -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "all", "payload": "broadcast"}
    ]
    factory = _factory(holder, inbound=inbound, idle=True)
    code = await cli._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=0.2,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 2  # a broadcast does not wake in directed-only mode


async def test_wait_directed_only_wakes_on_named(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [{"type": "chat", "sender": "A", "target": "B", "payload": "p"}]
    factory = _factory(holder, inbound=inbound)
    code = await cli._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 0


# --- who (directory) ---------------------------------------------------------


def test_parser_who() -> None:
    args = cli.build_parser().parse_args(["who", "--project", "quantum"])
    assert args.project == "quantum"
    assert args.func is cli._cmd_who


async def test_who_lists_project_agents(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snap: dict[str, Any] = {
        "type": "who_snapshot",
        "online_agents": ["quantum/claude-1", "quantum/codex-2", "other/gemini-3"],
    }
    # The leading non-snapshot message exercises the collect() filter's reject path.
    factory = _factory(holder, inbound=[{"type": "chat", "payload": "noise"}, snap])
    code = await cli._who(uri="ws://h", name="U", project="quantum", agent_factory=factory)
    assert code == 0
    out = capsys.readouterr().out
    assert "Online in quantum (2)" in out
    assert "quantum/claude-1" in out
    assert "other/gemini-3" not in out


async def test_who_lists_all_without_project(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    snap: dict[str, Any] = {"type": "who_snapshot", "online_agents": ["a", "b"]}
    factory = _factory(holder, inbound=[snap])
    code = await cli._who(uri="ws://h", name="U", agent_factory=factory)
    assert code == 0
    assert "Online (2)" in capsys.readouterr().out


async def test_who_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli._who(uri="ws://h", name="U", agent_factory=factory)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_who_returns_quietly_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.cli.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(
        holder, inbound=[{"type": "chat", "sender": "X", "payload": "noise"}], idle=False
    )
    code = await cli._who(uri="ws://h", name="U", agent_factory=factory)
    assert code == 0
    assert "Online" not in capsys.readouterr().out


def test_cmd_who_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="U", project=None, token=None)
    assert cli._cmd_who(ns) == 0


# --- state + relay --project (recovery) --------------------------------------


def test_parser_state() -> None:
    args = cli.build_parser().parse_args(["state", "--owner", "quantum"])
    assert args.owner == "quantum"
    assert args.func is cli._cmd_state


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
    code = await cli._state(uri="ws://h", name="U", owner="quantum", agent_factory=factory)
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
    assert await cli._state(uri="ws://h", name="U", agent_factory=factory) == 0
    assert "Active claims (1)" in capsys.readouterr().out


async def test_state_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    assert await cli._state(uri="ws://h", name="U", agent_factory=factory) == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_state_quiet_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.cli.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(holder, inbound=[{"type": "chat", "payload": "x"}], idle=False)
    assert await cli._state(uri="ws://h", name="U", agent_factory=factory) == 0
    assert "Active claims" not in capsys.readouterr().out


def test_cmd_state_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="U", owner=None, token=None)
    assert cli._cmd_state(ns) == 0


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
    assert await cli._state(uri="ws://h", name="U", agent_factory=factory) == 0
    assert "git=feature/x->main" in capsys.readouterr().out


def test_parser_git_claim() -> None:
    args = cli.build_parser().parse_args(
        ["git-claim", "T1", "--paths", "src", "--base", "develop", "--auto-release-on", "commit"]
    )
    assert args.func is cli._cmd_git_claim
    assert args.task_id == "T1"
    assert args.paths == ["src"]
    assert args.base == "develop"
    assert args.auto_release_on == "commit"


def test_cmd_git_claim_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli, "run_git_claim", fake)
    ns = argparse.Namespace(
        uri="ws://h",
        name="U",
        task_id="T1",
        paths=["src"],
        base="main",
        auto_release_on="merge",
        token=None,
    )
    assert cli._cmd_git_claim(ns) == 0


def test_parser_relay_project() -> None:
    args = cli.build_parser().parse_args(["relay", "feed.ndjson", "--project", "quantum"])
    assert args.project == "quantum"


def test_cmd_wait_derives_rx_name_for_bare_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_wait(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli, "_wait", fake_wait)
    monkeypatch.setattr("synapse_channel.cli.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="CEO",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
    )
    assert cli._cmd_wait(ns) == 0
    assert captured["name"] == "CEO-rx"  # bare identity gets a distinct receiver name
    assert captured["for_name"] == "CEO"


def test_cmd_wait_keeps_distinct_connect_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_wait(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli, "_wait", fake_wait)
    monkeypatch.setattr("synapse_channel.cli.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="CEO-rx",
        for_name="CEO",
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
    )
    assert cli._cmd_wait(ns) == 0
    assert captured["name"] == "CEO-rx"  # already distinct, left unchanged
    assert captured["for_name"] == "CEO"


async def test_wait_directed_only_wakes_on_ceo() -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "CEO", "target": "all", "payload": "directive"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 0  # a CEO broadcast wakes even a directed-only waiter


async def test_wait_directed_only_wakes_on_priority_broadcast() -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "all", "payload": "!", "priority": True}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 0  # a priority broadcast wakes even a directed-only waiter


def test_parser_send_priority() -> None:
    args = cli.build_parser().parse_args(["send", "hi", "--priority"])
    assert args.priority is True


async def test_send_marks_priority() -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, idle=False)
    code = await cli._send(
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


async def test_wait_exits_when_connection_drops() -> None:
    holder: list[FakeAgent] = []
    # idle=False → connect() returns at once (the socket closed); no message arrives.
    # With timeout=0 the old loop hung forever on the dead socket; the waiter must now
    # exit with code 3 so the caller re-arms instead of going dark.
    factory = _factory(holder, inbound=[], idle=False)
    code = await cli._wait(
        uri="ws://h", name="X-rx", for_name="X", timeout=0.0, agent_factory=factory
    )
    assert code == 3


def test_parser_wait_wake_jitter() -> None:
    args = cli.build_parser().parse_args(["wait", "--for", "B", "--wake-jitter", "3"])
    assert args.wake_jitter == 3.0
    assert cli.build_parser().parse_args(["wait", "--for", "B"]).wake_jitter == 8.0


async def test_wait_jitters_on_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[float, float]] = []

    def _rec(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    monkeypatch.setattr("synapse_channel.cli.random.uniform", _rec)
    holder: list[FakeAgent] = []
    # A CEO broadcast (target "all") wakes a directed-only waiter — and woke every
    # other terminal too, so the exit is jittered.
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "CEO", "target": "all", "payload": "go"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        wake_jitter=5.0,
        agent_factory=factory,
    )
    assert code == 0
    assert calls == [(0.0, 5.0)]  # jitter applied for the broadcast


async def test_wait_no_jitter_on_directed_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[float, float]] = []

    def _rec(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    monkeypatch.setattr("synapse_channel.cli.random.uniform", _rec)
    holder: list[FakeAgent] = []
    # A 1:1 directed message (target == for_name) — no herd, so no jitter.
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "B", "payload": "hi"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli._wait(
        uri="ws://h", name="B-rx", for_name="B", timeout=2.0, wake_jitter=5.0, agent_factory=factory
    )
    assert code == 0
    assert calls == []  # no jitter for a directed message


# --- A1: token via env / file -----------------------------------------------


def test_resolve_token_prefers_cli() -> None:
    assert cli._resolve_token(argparse.Namespace(token="cli", token_file=None)) == "cli"


def test_resolve_token_from_file(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("file-tok\n", encoding="utf-8")
    assert cli._resolve_token(argparse.Namespace(token=None, token_file=str(f))) == "file-tok"


def test_resolve_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    assert cli._resolve_token(argparse.Namespace(token=None, token_file=None)) == "env-tok"


def test_resolve_token_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "tok"
    f.write_text("file-tok", encoding="utf-8")
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    assert cli._resolve_token(argparse.Namespace(token="cli", token_file=str(f))) == "cli"
    assert cli._resolve_token(argparse.Namespace(token=None, token_file=str(f))) == "file-tok"


def test_resolve_token_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)
    assert cli._resolve_token(argparse.Namespace(token=None, token_file=None)) is None


def test_resolve_token_missing_file(tmp_path: Path) -> None:
    ns = argparse.Namespace(token=None, token_file=str(tmp_path / "nope"))
    with pytest.raises(FileNotFoundError):
        cli._resolve_token(ns)


def test_resolve_token_no_token_file_attr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    assert cli._resolve_token(argparse.Namespace(token=None)) == "env-tok"


def test_parser_adds_token_file_to_token_commands() -> None:
    args = cli.build_parser().parse_args(["send", "hi", "--token-file", "/x"])
    assert args.token_file == "/x"


def test_main_resolves_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_TOKEN", "env-tok")
    captured: dict[str, Any] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["token"] = args.token
        return 0

    monkeypatch.setattr(cli, "_cmd_board", fake)
    assert cli.main(["board"]) == 0
    assert captured["token"] == "env-tok"


def test_parser_hub_caps() -> None:
    args = cli.build_parser().parse_args(["hub", "--max-clients", "8", "--max-msg-kb", "32"])
    assert args.max_clients == 8
    assert args.max_msg_kb == 32


# --- A4: health probe -------------------------------------------------------


async def test_health_ok_when_ready() -> None:
    holder: list[FakeAgent] = []
    code = await cli._health(uri="ws://h", name="H", agent_factory=_factory(holder, ready=True))
    assert code == 0


async def test_health_fail_when_unreachable() -> None:
    holder: list[FakeAgent] = []
    code = await cli._health(uri="ws://h", name="H", agent_factory=_factory(holder, ready=False))
    assert code == 1


async def test_drop_message_is_noop() -> None:
    await cli._drop_message({"type": "x"})  # a no-op callback; must simply not raise


def test_parser_health() -> None:
    args = cli.build_parser().parse_args(["health", "--uri", "ws://x"])
    assert args.func is cli._cmd_health
    assert args.uri == "ws://x"


def test_cmd_health_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli, "_health", fake)
    assert cli._cmd_health(argparse.Namespace(uri="ws://h", name="H", token=None)) == 0


def test_parser_mcp() -> None:
    args = cli.build_parser().parse_args(["mcp", "--uri", "ws://x", "--name", "bridge"])
    assert args.func is cli._cmd_mcp
    assert args.uri == "ws://x"
    assert args.name == "bridge"


def _mcp_ns() -> argparse.Namespace:
    return argparse.Namespace(uri="ws://x", name="bridge", token=None)


def test_cmd_mcp_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli, "serve_stdio", fake)
    assert cli._cmd_mcp(_mcp_ns()) == 0


def test_cmd_mcp_reports_missing_extra(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake(**kwargs: Any) -> int:
        raise RuntimeError("pip install 'synapse-channel[mcp]'")

    monkeypatch.setattr(cli, "serve_stdio", fake)
    assert cli._cmd_mcp(_mcp_ns()) == 1
    assert "[mcp]" in capsys.readouterr().err


def test_cmd_mcp_handles_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "serve_stdio", fake)
    assert cli._cmd_mcp(_mcp_ns()) == 0


def test_parser_git_hook() -> None:
    args = cli.build_parser().parse_args(["git-hook", "install", "--name", "ME"])
    assert args.func is cli._cmd_git_hook
    assert args.action == "install"
    assert args.name == "ME"


def test_cmd_git_hook_dispatches(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "install_hooks", lambda **kwargs: ["installed post-commit"])
    ns = argparse.Namespace(
        action="install", uri="ws://h", name="ME", token=None, token_file=None, synapse_bin=None
    )
    assert cli._cmd_git_hook(ns) == 0
    assert "installed post-commit" in capsys.readouterr().out


def test_cmd_git_hook_reports_git_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(**kwargs: Any) -> list[str]:
        raise GitError("not a git repository")

    monkeypatch.setattr(cli, "install_hooks", boom)
    ns = argparse.Namespace(
        action="install", uri="ws://h", name="ME", token=None, token_file=None, synapse_bin=None
    )
    assert cli._cmd_git_hook(ns) == 1
    assert "not a git repository" in capsys.readouterr().err


def test_parser_git_release() -> None:
    args = cli.build_parser().parse_args(["git-release", "--trigger", "merge"])
    assert args.func is cli._cmd_git_release
    assert args.trigger == "merge"


def test_cmd_git_release_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli, "run_git_release", fake)
    ns = argparse.Namespace(task_id=None, uri="ws://h", name="ME", trigger="commit", token=None)
    assert cli._cmd_git_release(ns) == 0


def test_parser_conflicts() -> None:
    args = cli.build_parser().parse_args(["conflicts", "--check-diff"])
    assert args.func is cli._cmd_conflicts
    assert args.check_diff is True


def test_cmd_conflicts_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_conflicts", fake)
    ns = argparse.Namespace(uri="ws://h", name="ME", token=None, check_diff=True)
    assert cli._cmd_conflicts(ns) == 0
    assert captured["check_diff"] is True
