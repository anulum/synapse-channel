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


# --- worker task classes and tiers -------------------------------------------


def test_parser_worker_task_class() -> None:
    worker = cli.build_parser().parse_args(
        ["worker", "--task-class", "reason", "--task-class", "heavy"]
    )
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


def test_parser_relay_for_flag() -> None:
    relay = cli.build_parser().parse_args(["relay", "feed.ndjson", "--for", "B"])
    assert relay.for_name == "B"


# --- relay --project ---------------------------------------------------------


def test_parser_relay_project() -> None:
    args = cli.build_parser().parse_args(["relay", "feed.ndjson", "--project", "quantum"])
    assert args.project == "quantum"


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

    monkeypatch.setattr(cli, "_cmd_worker", fake)
    assert cli.main(["worker"]) == 0
    assert captured["token"] == "env-tok"


def test_parser_hub_caps() -> None:
    args = cli.build_parser().parse_args(["hub", "--max-clients", "8", "--max-msg-kb", "32"])
    assert args.max_clients == 8
    assert args.max_msg_kb == 32
