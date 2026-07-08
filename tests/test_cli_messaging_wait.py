# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the messaging CLI commands (send/wait/listen)

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_messaging
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.wake_capability import WAKE_PASSIVE
from synapse_channel.mailbox_cursor import load_cursor


async def _wait_for_presence(observer: AgentHandle, name: str) -> None:
    await observer.recorder.wait_for(
        lambda message: message.get("type") == "presence_update" and message.get("agent") == name
    )


async def _send_chat(
    uri: str, sender: str, target: str, payload: str, *, priority: bool = False
) -> None:
    handle = await connect_agent(sender, uri)
    try:
        await handle.agent.chat(payload, target=target, priority=priority)
    finally:
        await close_agents(handle)


async def test_wait_returns_on_addressed_message(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(uri=uri, name="B-rx", for_name="B", timeout=2.0)
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "B", "wake up")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0
    assert "A: wake up" in capsys.readouterr().out


async def test_wait_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_messaging._wait(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="B",
        for_name="B",
        timeout=1.0,
        ready_timeout=0.1,
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_wait_times_out_with_nothing() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        code = await cli_messaging._wait(
            uri=uri,
            name="B-rx",
            for_name="B",
            timeout=0.05,
            poll_interval=0.01,
        )
    assert code == 2


def test_cmd_wait_dispatches_with_for_default(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="X",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_wait(ns) == 1
    assert "[X-rx] Could not reach hub" in capsys.readouterr().out


def test_cmd_wait_defaults_to_passive_wake_capability() -> None:
    captured: dict[str, object] = {}

    async def wait_once(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="X",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )

    assert (
        cli_messaging._cmd_wait(
            ns, wait_runner=wait_once, async_runner=lambda coro: asyncio.run(coro)
        )
        == 0
    )
    assert captured["wake_capability"] == WAKE_PASSIVE


def test_cmd_wait_refuses_legacy_project_scoped_terminal_waiter_before_provider_probe(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A legacy ``--for user`` terminal sidecar refuses before checking providers."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    provider_dir = runtime / "synapse-provider-tmux"
    provider_dir.mkdir()
    (provider_dir / "user_terminal-38253.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    calls: list[object] = []

    async def wait_once(**kwargs: object) -> int:
        calls.append(kwargs)
        return 1

    ns = argparse.Namespace(
        uri="ws://h",
        name="user/terminal-38253-rx",
        for_name="user",
        timeout=0.0,
        directed_only=True,
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )

    assert (
        cli_messaging._cmd_wait(
            ns, wait_runner=wait_once, async_runner=lambda coro: asyncio.run(coro)
        )
        == 0
    )
    assert not calls
    out = capsys.readouterr().out
    assert "legacy broad project wait for user" in out
    assert "user/terminal-38253" in out


def test_cmd_wait_refuses_legacy_project_scoped_terminal_sidecar(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Old shell functions cannot recreate a broad project wake loop."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    calls: list[object] = []

    async def wait_once(**kwargs: object) -> int:
        calls.append(kwargs)
        return 1

    ns = argparse.Namespace(
        uri="ws://h",
        name="user/terminal-15627-rx",
        for_name="user",
        timeout=0.0,
        directed_only=True,
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )

    assert (
        cli_messaging._cmd_wait(
            ns, wait_runner=wait_once, async_runner=lambda coro: asyncio.run(coro)
        )
        == 0
    )
    assert not calls
    out = capsys.readouterr().out
    assert "legacy broad project wait for user" in out
    assert "user/terminal-15627" in out


async def test_wait_wakes_on_a_held_role(capsys: pytest.CaptureFixture[str]) -> None:
    # A directed-only waiter armed for its instance name plus a role wakes on a
    # message addressed to the role — the fix for a role-addressed message being
    # silently dropped when it matched no instance name.
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="proj-claude-rx",
                for_name="proj/claude",
                timeout=2.0,
                directed_only=True,
                roles=("proj/coordinator",),
            )
        )
        try:
            await _wait_for_presence(observer, "proj-claude-rx")
            await _send_chat(uri, "peer", "proj/coordinator", "role ping")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0
    assert "peer: role ping" in capsys.readouterr().out


async def test_wait_directed_only_ignores_a_role_it_does_not_hold() -> None:
    # A message to a role this waiter does NOT hold must not wake it (no wake storm).
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="proj-claude-rx",
                for_name="proj/claude",
                timeout=0.2,
                poll_interval=0.01,
                directed_only=True,
                roles=("proj/git",),
            )
        )
        try:
            await _wait_for_presence(observer, "proj-claude-rx")
            await _send_chat(uri, "peer", "proj/coordinator", "not yours")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 2


def test_cmd_wait_dispatches_with_roles(capsys: pytest.CaptureFixture[str]) -> None:
    # Role flags are normalised (blanks dropped) and threaded through; an
    # unreachable hub still returns 1 but exercises the role-parsing path.
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="X",
        for_name=None,
        timeout=0.0,
        directed_only=True,
        role=["proj/coordinator", "  ", "proj/git"],
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_wait(ns) == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_wait_ignores_own_messages() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=0.05,
                poll_interval=0.01,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "B", "all", "x")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 2


async def test_wait_directed_only_ignores_broadcast() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=0.05,
                directed_only=True,
                poll_interval=0.01,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "all", "broadcast")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 2  # a broadcast does not wake in directed-only mode


async def test_wait_directed_only_wakes_on_named(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                directed_only=True,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "B", "p")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0
    assert "A: p" in capsys.readouterr().out


def test_cmd_wait_derives_rx_name_for_bare_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="CEO",
        for_name=None,
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_wait(ns) == 1
    assert "[CEO-rx] Could not reach hub" in capsys.readouterr().out


def test_cmd_wait_keeps_distinct_connect_name(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="CEO-rx",
        for_name="CEO",
        timeout=0.0,
        directed_only=False,
        wake_jitter=0.0,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_messaging._cmd_wait(ns) == 1
    assert "[CEO-rx] Could not reach hub" in capsys.readouterr().out


async def test_wait_directed_only_wakes_on_ceo() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                directed_only=True,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "CEO", "all", "directive")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0  # a CEO broadcast wakes even a directed-only waiter


async def test_wait_directed_only_wakes_on_priority_broadcast() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                directed_only=True,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "all", "!", priority=True)
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0  # a priority broadcast wakes even a directed-only waiter


async def test_wait_exits_when_connection_drops() -> None:
    manager: AbstractAsyncContextManager[tuple[SynapseHub, str]] = running_hub(SynapseHub())
    _hub, uri = await manager.__aenter__()
    observer = await connect_agent("OBSERVER", uri)
    wait_task = asyncio.create_task(
        cli_messaging._wait(
            uri=uri,
            name="X-rx",
            for_name="X",
            timeout=0.0,
            poll_interval=0.01,
        )
    )
    await _wait_for_presence(observer, "X-rx")
    await close_agents(observer)
    await manager.__aexit__(None, None, None)
    code = await wait_task
    assert code == 3


async def test_wait_jitters_on_broadcast() -> None:
    calls: list[tuple[float, float]] = []

    def _rec(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                directed_only=True,
                wake_jitter=5.0,
                jitter_func=_rec,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "CEO", "all", "go")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0
    assert calls == [(0.0, 5.0)]  # jitter applied for the broadcast


async def test_wait_no_jitter_on_directed_wake() -> None:
    calls: list[tuple[float, float]] = []

    def _rec(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        wait_task = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="B-rx",
                for_name="B",
                timeout=2.0,
                wake_jitter=5.0,
                jitter_func=_rec,
            )
        )
        try:
            await _wait_for_presence(observer, "B-rx")
            await _send_chat(uri, "A", "B", "hi")
            code = await wait_task
        finally:
            await close_agents(observer)

    assert code == 0
    assert calls == []  # no jitter for a directed message


async def test_superseded_wait_yields_instead_of_rearming(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A waiter displaced by a takeover exits with the yield verdict (4).

    This pins the waiter-fight defect: the displaced side used to report a
    generic drop (3), and its arm loop reconnected with a takeover of its own —
    two waiters for one identity then stole the name from each other until the
    hub quarantined it, while the loser burned reconnect attempts for days.
    """
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        first_wait = asyncio.create_task(
            cli_messaging._wait(
                uri=uri,
                name="X-rx",
                for_name="X",
                timeout=0.0,
                poll_interval=0.01,
            )
        )
        try:
            await _wait_for_presence(observer, "X-rx")
            # a second waiter claims the same identity: hub evicts the first
            second_wait = asyncio.create_task(
                cli_messaging._wait(
                    uri=uri,
                    name="X-rx",
                    for_name="X",
                    timeout=0.0,
                    poll_interval=0.01,
                )
            )
            code = await asyncio.wait_for(first_wait, timeout=5.0)
            assert code == 4
            assert "superseded by a newer waiter; yielding" in capsys.readouterr().out
            # the new holder is still armed and still wakes
            await _send_chat(uri, "A", "X", "wake")
            assert await asyncio.wait_for(second_wait, timeout=5.0) == 0
        finally:
            await close_agents(observer)


async def test_arm_disarms_after_a_takeover_displacement(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The arm loop ends on the yield verdict instead of fighting for the name."""
    from synapse_channel import cli_arm

    async def yield_now(**_kwargs: object) -> int:
        return 4

    code = await asyncio.wait_for(
        cli_arm._arm(
            uri="ws://unused",
            name="X-rx",
            for_name="X",
            reconnect_delay=0.0,
            wait_runner=yield_now,
        ),
        timeout=5.0,
    )
    assert code == 0
    assert "a newer waiter holds this name; disarming" in capsys.readouterr().out


async def test_takeover_refused_at_handshake_yields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A takeover refused by cooldown/quarantine is a yield, not an outage retry."""

    class _RefusedAgent:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.running = True
            self.last_close_code: int | None = 4014
            self.last_close_reason = "takeover quarantine"

        async def connect(self) -> None:
            return None

        async def wait_until_ready(self, timeout: float) -> bool:
            del timeout
            return False

    code = await cli_messaging._wait(
        uri="ws://unused",
        name="X-rx",
        for_name="X",
        timeout=0.0,
        agent_factory=_RefusedAgent,  # type: ignore[arg-type]
    )
    assert code == 4
    assert "takeover" in capsys.readouterr().out


async def test_wait_mailbox_replays_a_gap_message_and_persists_the_cursor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A directed message to BOB lands while no BOB waiter is connected (the reconnect gap).
    # A mailbox waiter connecting as BOB-rx is replayed the message, wakes on it, and writes
    # the advanced cursor — the gap message is not lost until an unrelated wake drains it.
    store = EventStore(tmp_path / "events.db")
    cursor = tmp_path / "cursor"
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        await _send_chat(uri, "SENDER", "BOB", "gap-message")
        code = await cli_messaging._wait(
            uri=uri,
            name="BOB-rx",
            for_name="BOB",
            timeout=2.0,
            directed_only=True,
            mailbox=True,
            mailbox_cursor_path=cursor,
        )
    store.close()
    assert code == 0
    assert "gap-message" in capsys.readouterr().out
    assert load_cursor(cursor) > 0


async def test_wait_mailbox_resumes_from_the_persisted_cursor(tmp_path: Path) -> None:
    # After a first mailbox waiter consumes the gap message and persists its cursor, a second
    # waiter seeded from that cursor is not replayed the same message again — it times out with
    # nothing new rather than waking on stale backlog, which is what stops a re-arm wake storm.
    store = EventStore(tmp_path / "events.db")
    cursor = tmp_path / "cursor"
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        await _send_chat(uri, "SENDER", "BOB", "old-gap")
        first = await cli_messaging._wait(
            uri=uri,
            name="BOB-rx",
            for_name="BOB",
            timeout=2.0,
            directed_only=True,
            mailbox=True,
            mailbox_cursor_path=cursor,
        )
        assert first == 0
        assert load_cursor(cursor) > 0
        second = await cli_messaging._wait(
            uri=uri,
            name="BOB-rx",
            for_name="BOB",
            timeout=0.5,
            directed_only=True,
            mailbox=True,
            mailbox_cursor_path=cursor,
        )
    store.close()
    assert second == 2
