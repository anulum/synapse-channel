# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the messaging CLI commands (send/wait/arm/listen)

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from synapse_channel import cli, cli_messaging


class FakeAgent:
    """Stand-in for SynapseAgent used by the send/wait/listen flow tests."""

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
        self.chat_priorities: list[bool] = []
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
        self.chat_priorities.append(priority)


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


def test_parser_send_and_listen() -> None:
    send = cli.build_parser().parse_args(
        ["send", "hello", "--target", "FAST", "--wait-seconds", "0"]
    )
    assert send.message == "hello"
    assert send.target == "FAST"
    assert send.wait_seconds == 0.0

    listen = cli.build_parser().parse_args(["listen", "--name", "WATCH"])
    assert listen.name == "WATCH"


def test_parser_listen_for_flag() -> None:
    listen = cli.build_parser().parse_args(["listen", "--name", "B", "--for", "B"])
    assert listen.for_name == "B"
    assert listen.func is cli_messaging._cmd_listen


def test_parser_wait() -> None:
    args = cli.build_parser().parse_args(["wait", "--name", "X", "--for", "Y", "--timeout", "5"])
    assert args.name == "X"
    assert args.for_name == "Y"
    assert args.timeout == 5.0
    assert args.func is cli_messaging._cmd_wait


def test_parser_wait_directed_only() -> None:
    args = cli.build_parser().parse_args(["wait", "--for", "B", "--directed-only"])
    assert args.directed_only is True


def test_parser_send_priority() -> None:
    args = cli.build_parser().parse_args(["send", "hi", "--priority"])
    assert args.priority is True


def test_parser_wait_wake_jitter() -> None:
    args = cli.build_parser().parse_args(["wait", "--for", "B", "--wake-jitter", "3"])
    assert args.wake_jitter == 3.0
    assert cli.build_parser().parse_args(["wait", "--for", "B"]).wake_jitter == 8.0


def test_parser_arm_is_persistent_directed_waiter() -> None:
    args = cli.build_parser().parse_args(["arm", "--name", "B", "--for", "B"])
    assert args.name == "B"
    assert args.for_name == "B"
    assert args.directed_only is True
    assert args.func is cli_messaging._cmd_arm


def test_parser_arm_broadcasts_opt_in() -> None:
    args = cli.build_parser().parse_args(["arm", "--for", "B", "--broadcasts"])
    assert args.directed_only is False


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
    code = await cli_messaging._send(
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
    code = await cli_messaging._send(
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
    code = await cli_messaging._send(
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
    code = await cli_messaging._send(
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
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="USER",
        target="all",
        message="hi",
        wait_seconds=0.0,
        priority=False,
        token=None,
    )
    assert cli_messaging._cmd_send(ns) == 0


async def test_send_threads_token_to_agent() -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder)
    await cli_messaging._send(
        uri="ws://h",
        name="U",
        target="all",
        message="hi",
        wait_seconds=0.0,
        agent_factory=factory,
        token="s3cret",
    )
    assert holder[0].token == "s3cret"


async def test_send_marks_priority() -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, idle=False)
    code = await cli_messaging._send(
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


# --- listen ------------------------------------------------------------------


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


# --- wait (wake trigger) -----------------------------------------------------


async def test_wait_returns_on_addressed_message(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "presence_update", "sender": "hub"},  # not a chat — ignored
        {"type": "chat", "sender": "A", "target": "B", "payload": "wake up"},
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
        uri="ws://h", name="B-rx", for_name="B", timeout=2.0, agent_factory=factory
    )
    assert code == 0
    assert "A: wake up" in capsys.readouterr().out


async def test_wait_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli_messaging._wait(
        uri="ws://h", name="B", for_name="B", timeout=1.0, agent_factory=factory
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_wait_times_out_with_nothing() -> None:
    holder: list[FakeAgent] = []
    # idle=True keeps the connection up so this exercises the timeout path (code 2),
    # distinct from a dropped connection (code 3).
    factory = _factory(holder, inbound=[], idle=True)
    code = await cli_messaging._wait(
        uri="ws://h", name="B", for_name="B", timeout=0.2, agent_factory=factory
    )
    assert code == 2


def test_cmd_wait_dispatches_with_for_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="X",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
    )
    assert cli_messaging._cmd_wait(ns) == 0


async def test_wait_ignores_own_messages() -> None:
    holder: list[FakeAgent] = []
    # A broadcast whose sender is our own identity (we send as for_name) must not wake us.
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "B", "target": "all", "payload": "x"}
    ]
    factory = _factory(holder, inbound=inbound, idle=True)
    code = await cli_messaging._wait(
        uri="ws://h", name="B-rx", for_name="B", timeout=0.2, agent_factory=factory
    )
    assert code == 2


async def test_wait_directed_only_ignores_broadcast() -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "all", "payload": "broadcast"}
    ]
    factory = _factory(holder, inbound=inbound, idle=True)
    code = await cli_messaging._wait(
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
    code = await cli_messaging._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 0


def test_cmd_wait_derives_rx_name_for_bare_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_wait(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli_messaging, "_wait", fake_wait)
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="CEO",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
    )
    assert cli_messaging._cmd_wait(ns) == 0
    assert captured["name"] == "CEO-rx"  # bare identity gets a distinct receiver name
    assert captured["for_name"] == "CEO"


def test_cmd_wait_keeps_distinct_connect_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_wait(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli_messaging, "_wait", fake_wait)
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="CEO-rx",
        for_name="CEO",
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
    )
    assert cli_messaging._cmd_wait(ns) == 0
    assert captured["name"] == "CEO-rx"  # already distinct, left unchanged
    assert captured["for_name"] == "CEO"


async def test_wait_directed_only_wakes_on_ceo() -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "CEO", "target": "all", "payload": "directive"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
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
    code = await cli_messaging._wait(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        timeout=2.0,
        directed_only=True,
        agent_factory=factory,
    )
    assert code == 0  # a priority broadcast wakes even a directed-only waiter


async def test_wait_exits_when_connection_drops() -> None:
    holder: list[FakeAgent] = []
    # idle=False → connect() returns at once (the socket closed); no message arrives.
    # With timeout=0 the old loop hung forever on the dead socket; the waiter must now
    # exit with code 3 so the caller re-arms instead of going dark.
    factory = _factory(holder, inbound=[], idle=False)
    code = await cli_messaging._wait(
        uri="ws://h", name="X-rx", for_name="X", timeout=0.0, agent_factory=factory
    )
    assert code == 3


async def test_wait_jitters_on_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[float, float]] = []

    def _rec(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    monkeypatch.setattr("synapse_channel.cli_messaging.random.uniform", _rec)
    holder: list[FakeAgent] = []
    # A CEO broadcast (target "all") wakes a directed-only waiter — and woke every
    # other terminal too, so the exit is jittered.
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "CEO", "target": "all", "payload": "go"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
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

    monkeypatch.setattr("synapse_channel.cli_messaging.random.uniform", _rec)
    holder: list[FakeAgent] = []
    # A 1:1 directed message (target == for_name) — no herd, so no jitter.
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "B", "payload": "hi"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._wait(
        uri="ws://h", name="B-rx", for_name="B", timeout=2.0, wake_jitter=5.0, agent_factory=factory
    )
    assert code == 0
    assert calls == []  # no jitter for a directed message


# --- arm (persistent wake trigger) -------------------------------------------


async def test_arm_rearms_after_each_wake(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        {"type": "chat", "sender": "A", "target": "B", "payload": "wake"}
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_messaging._arm(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        max_wakes=2,
        reconnect_delay=0.0,
        agent_factory=factory,
    )
    assert code == 0
    assert len(holder) == 2
    assert capsys.readouterr().out.count("A: wake") == 2


def test_cmd_arm_derives_rx_name_for_bare_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_arm(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli_messaging, "_arm", fake_arm)
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="B",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
    )
    assert cli_messaging._cmd_arm(ns) == 0
    assert captured["name"] == "B-rx"
    assert captured["for_name"] == "B"


def test_cmd_arm_keeps_distinct_connect_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_arm(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "coro"

    monkeypatch.setattr(cli_messaging, "_arm", fake_arm)
    monkeypatch.setattr("synapse_channel.cli_messaging.asyncio.run", lambda coro: 0)
    ns = argparse.Namespace(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
    )
    assert cli_messaging._cmd_arm(ns) == 0
    assert captured["name"] == "B-rx"
    assert captured["for_name"] == "B"
