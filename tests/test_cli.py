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
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.client import DEFAULT_HUB_URI
from synapse_channel.llm_worker import DEFAULT_OLLAMA_BASE_URL


class FakeAgent:
    """Configurable stand-in for SynapseAgent used by send/listen tests."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool,
        ready: bool = True,
        inbound: list[dict[str, Any]] | None = None,
        idle: bool = True,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.running = True
        self.chats: list[tuple[str, str]] = []
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

    async def chat(self, payload: str, *, target: str = "all") -> None:
        self.chats.append((target, payload))


def _factory(
    holder: list[FakeAgent],
    *,
    ready: bool = True,
    inbound: list[dict[str, Any]] | None = None,
    idle: bool = True,
) -> Callable[..., Any]:
    def make(name: str, callback: Any, *, uri: str, verbose: bool) -> Any:
        agent = FakeAgent(
            name, callback, uri=uri, verbose=verbose, ready=ready, inbound=inbound, idle=idle
        )
        holder.append(agent)
        return agent

    return make


# --- parser ------------------------------------------------------------------


def test_parser_hub_defaults() -> None:
    args = cli.build_parser().parse_args(["hub"])
    assert args.host == "localhost"
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


def test_main_version_exits() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])


def test_main_routes_to_team(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_team", lambda **kwargs: 9)
    assert cli.main(["team", "--no-workers"]) == 9


def test_main_routes_to_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    assert cli.main(["hub", "--port", "9000"]) == 0


# --- hub / worker handlers ---------------------------------------------------


def test_cmd_hub_runs_and_handles_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    ns = argparse.Namespace(host="localhost", port=8876)
    assert cli._cmd_hub(ns) == 0

    def interrupt(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run", interrupt)
    assert cli._cmd_hub(ns) == 0


def _worker_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "name": "FAST",
        "uri": DEFAULT_HUB_URI,
        "provider": "rule",
        "model": "llama3",
        "base_url": DEFAULT_OLLAMA_BASE_URL,
        "api_key_env": "OPENAI_API_KEY",
        "max_context": 8,
        "reply_target_mode": "all",
        "min_reply_interval": 0.7,
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
    ns = argparse.Namespace(port=8876, no_workers=False, fast_model=None, reason_model=None)
    assert cli._cmd_team(ns) == 4


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
        uri="ws://h", name="USER", target="all", message="hi", wait_seconds=0.0
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
    ns = argparse.Namespace(uri="ws://h", name="USER")
    assert cli._cmd_listen(ns) == 0

    def interrupt(coro: Any) -> int:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.cli.asyncio.run", interrupt)
    assert cli._cmd_listen(ns) == 0
