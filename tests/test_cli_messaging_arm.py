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
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli_arm
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.wake_capability import WAKE_PASSIVE


async def _wait_for_presence_count(observer: AgentHandle, name: str, count: int) -> None:
    await observer.recorder.wait_for(
        lambda _message: (
            len(
                [
                    item
                    for item in observer.recorder.messages
                    if item.get("type") == "presence_update" and item.get("agent") == name
                ]
            )
            >= count
        )
    )


async def _send_chat(uri: str, sender: str, target: str, payload: str) -> None:
    handle = await connect_agent(sender, uri)
    try:
        await handle.agent.chat(payload, target=target)
    finally:
        await close_agents(handle)


async def test_arm_rearms_after_each_wake(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        arm_task = asyncio.create_task(
            cli_arm._arm(
                uri=uri,
                name="B-rx",
                for_name="B",
                max_wakes=2,
                reconnect_delay=0.0,
            )
        )
        try:
            await _wait_for_presence_count(observer, "B-rx", 1)
            await _send_chat(uri, "A", "B", "wake")
            deadline = asyncio.get_event_loop().time() + 2.0
            while not arm_task.done() and asyncio.get_event_loop().time() < deadline:
                await _send_chat(uri, "A", "B", "wake")
                await asyncio.sleep(0.05)
            code = await asyncio.wait_for(arm_task, timeout=0.5)
        finally:
            await close_agents(observer)

    assert code == 0
    assert capsys.readouterr().out.count("A: wake") == 2


async def test_arm_retries_after_non_wake_result() -> None:
    results = iter([1, 0])
    sleeps: list[float] = []
    original_sleep = asyncio.sleep

    async def wait_once(**_: Any) -> int:
        return next(results)

    async def sleep_once(delay: float) -> None:
        sleeps.append(delay)
        await original_sleep(0)

    code = await cli_arm._arm(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        max_wakes=1,
        reconnect_delay=0.25,
        wait_runner=wait_once,
        sleep_runner=sleep_once,
    )

    assert code == 0
    assert sleeps == [0.25]


async def test_arm_retries_immediately_when_reconnect_delay_is_zero() -> None:
    results = iter([1, 0])
    calls = 0

    async def wait_once(**_: Any) -> int:
        nonlocal calls
        calls += 1
        return next(results)

    code = await cli_arm._arm(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        max_wakes=1,
        reconnect_delay=0.0,
        wait_runner=wait_once,
    )

    assert code == 0
    assert calls == 2


def test_cmd_arm_derives_rx_name_for_bare_identity() -> None:
    captured: dict[str, Any] = {}

    async def arm_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    def run_once(coro: Coroutine[Any, Any, int]) -> int:
        return asyncio.run(coro)

    ns = argparse.Namespace(
        uri="ws://h",
        name="B",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=run_once) == 0
    assert captured["name"] == "B-rx"
    assert captured["for_name"] == "B"


def test_cmd_arm_yields_when_tmux_provider_is_live(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A passive arm must not compete with an active tmux pane-bridge waker."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    provider_dir = runtime / "synapse-provider-tmux"
    provider_dir.mkdir()
    (provider_dir / "B.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    calls: list[Any] = []

    async def arm_once(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="B",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=lambda c: asyncio.run(c)) == 0
    assert not calls
    out = capsys.readouterr().out
    assert "active tmux provider detected for B" in out
    assert "Yielding plain passive arm" in out


def test_cmd_arm_refuses_legacy_project_scoped_terminal_waiter_before_provider_probe(
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

    calls: list[Any] = []

    async def arm_once(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="user/terminal-38253-rx",
        for_name="user",
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=lambda c: asyncio.run(c)) == 0
    assert not calls
    out = capsys.readouterr().out
    assert "legacy broad project wait for user" in out
    assert "user/terminal-38253" in out


def test_cmd_arm_refuses_legacy_project_scoped_terminal_sidecar(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Old shell functions cannot recreate a broad project wake loop."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    calls: list[Any] = []

    async def arm_once(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="user/terminal-15627-rx",
        for_name="user",
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=lambda c: asyncio.run(c)) == 0
    assert not calls
    out = capsys.readouterr().out
    assert "legacy broad project wait for user" in out
    assert "user/terminal-15627" in out


def test_cmd_arm_arms_when_tmux_provider_pidfile_is_dead(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A stale pidfile with no live process does not block passive arming."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    provider_dir = runtime / "synapse-provider-tmux"
    provider_dir.mkdir()
    (provider_dir / "B.pid").write_text("999999999\n", encoding="utf-8")

    captured: dict[str, Any] = {}

    async def arm_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="B",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=lambda c: asyncio.run(c)) == 0
    assert captured["name"] == "B-rx"
    assert captured["for_name"] == "B"
    assert "active tmux provider detected" not in capsys.readouterr().out


def test_cmd_arm_arms_when_no_tmux_provider(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Headless identities arm normally when no tmux provider owns the -rx."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    captured: dict[str, Any] = {}

    async def arm_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="B",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=lambda c: asyncio.run(c)) == 0
    assert captured["name"] == "B-rx"
    assert captured["for_name"] == "B"
    assert "active tmux provider detected" not in capsys.readouterr().out


def test_cmd_arm_threads_roles_dropping_blanks() -> None:
    captured: dict[str, Any] = {}

    async def arm_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    def run_once(coro: Coroutine[Any, Any, int]) -> int:
        return asyncio.run(coro)

    ns = argparse.Namespace(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        directed_only=True,
        role=["proj/coordinator", "  ", "proj/git"],
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=run_once) == 0
    assert captured["roles"] == ("proj/coordinator", "proj/git")


async def test_arm_passes_roles_to_each_wait() -> None:
    captured: dict[str, Any] = {}

    async def wait_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 4  # a takeover ends the loop after one wait

    code = await cli_arm._arm(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        roles=("proj/coordinator",),
        reconnect_delay=0.0,
        wait_runner=wait_once,
    )
    assert code == 0
    assert captured["roles"] == ("proj/coordinator",)


async def test_arm_passes_passive_wake_capability_to_each_wait() -> None:
    captured: dict[str, Any] = {}

    async def wait_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 4

    code = await cli_arm._arm(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        reconnect_delay=0.0,
        wait_runner=wait_once,
    )
    assert code == 0
    assert captured["wake_capability"] == WAKE_PASSIVE


def test_cmd_arm_handles_keyboard_interrupt(capsys: pytest.CaptureFixture[str]) -> None:
    def stop(coro: Coroutine[Any, Any, int]) -> int:
        coro.close()
        raise KeyboardInterrupt

    async def arm_once(**_: Any) -> int:
        return 0

    ns = argparse.Namespace(
        uri="ws://h",
        name="B",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
    )

    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=stop) == 0
    assert "stopped arming for B" in capsys.readouterr().out


def test_cmd_arm_keeps_distinct_connect_name() -> None:
    captured: dict[str, Any] = {}

    async def arm_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    def run_once(coro: Coroutine[Any, Any, int]) -> int:
        return asyncio.run(coro)

    ns = argparse.Namespace(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=run_once) == 0
    assert captured["name"] == "B-rx"
    assert captured["for_name"] == "B"


# --- the owner-pid leash: a waiter must not outlive the terminal it wakes ---


async def test_arm_refuses_to_start_for_a_dead_owner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A waiter armed for an already-gone shell exits before connecting."""
    code = await cli_arm._arm(
        uri="ws://unused",
        name="B-rx",
        for_name="B",
        owner_pid=999_999_999,
        owner_probe=lambda _pid: False,
    )
    assert code == 0
    assert "already gone; not arming" in capsys.readouterr().out


async def test_arm_disarms_when_the_owner_dies_mid_wait(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The owner watchdog cancels the armed wait the moment the shell exits.

    This pins the phantom-presence defect: detached (nohup + disown) waiters
    survived their terminals for days, each holding a live hub socket, until a
    ~30-terminal workstation reported 200 online identities.
    """
    probes = iter([True, False])

    async def wait_forever(**_kwargs: Any) -> int:
        await asyncio.sleep(3600)
        return 0

    code = await asyncio.wait_for(
        cli_arm._arm(
            uri="ws://unused",
            name="B-rx",
            for_name="B",
            owner_pid=42,
            owner_probe=lambda _pid: next(probes),
            owner_check_interval=0.01,
            wait_runner=wait_forever,
        ),
        timeout=5.0,
    )
    assert code == 0
    assert "owner pid 42 exited; disarming" in capsys.readouterr().out


async def test_arm_keeps_waking_while_the_owner_lives() -> None:
    """A live owner never interrupts the wake loop; wakes still count down."""
    wakes = 0

    async def wake_now(**_kwargs: Any) -> int:
        nonlocal wakes
        wakes += 1
        return 0

    code = await asyncio.wait_for(
        cli_arm._arm(
            uri="ws://unused",
            name="B-rx",
            for_name="B",
            max_wakes=3,
            owner_pid=42,
            owner_probe=lambda _pid: True,
            owner_check_interval=30.0,
            wait_runner=wake_now,
        ),
        timeout=5.0,
    )
    assert code == 0
    assert wakes == 3


def test_pid_alive_reports_this_process_and_rejects_nonsense() -> None:
    import os

    assert cli_arm.pid_alive(os.getpid())
    assert not cli_arm.pid_alive(0)
    assert not cli_arm.pid_alive(-5)


def test_pid_alive_treats_permission_errors_as_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pid owned by another user exists even though signalling it is denied."""

    def deny(_pid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr("os.kill", deny)
    assert cli_arm.pid_alive(1234)


def test_pid_alive_reports_a_vanished_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def vanish(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("os.kill", vanish)
    assert not cli_arm.pid_alive(1234)


def test_cmd_arm_forwards_the_owner_pid() -> None:
    captured: dict[str, Any] = {}

    async def arm_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    def run_once(coro: Coroutine[Any, Any, int]) -> int:
        return asyncio.run(coro)

    ns = argparse.Namespace(
        uri="ws://h",
        name="B",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=4321,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=run_once) == 0
    assert captured["owner_pid"] == 4321


def test_cmd_arm_mailbox_derives_a_cursor_keyed_by_for_name() -> None:
    captured: dict[str, Any] = {}

    async def arm_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    def run_once(coro: Coroutine[Any, Any, int]) -> int:
        return asyncio.run(coro)

    ns = argparse.Namespace(
        uri="ws://h",
        name="proj/agent",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
        mailbox=True,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=run_once) == 0
    assert captured["mailbox"] is True
    # The cursor is keyed by for_name (the waited-on identity), not the -rx connection name.
    assert captured["mailbox_cursor_path"] is not None
    assert captured["mailbox_cursor_path"].name == "proj%2Fagent"


def test_cmd_arm_without_mailbox_threads_no_cursor() -> None:
    captured: dict[str, Any] = {}

    async def arm_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    def run_once(coro: Coroutine[Any, Any, int]) -> int:
        return asyncio.run(coro)

    ns = argparse.Namespace(
        uri="ws://h",
        name="B",
        for_name=None,
        directed_only=True,
        wake_jitter=0.0,
        reconnect_delay=0.0,
        max_wakes=None,
        token=None,
        owner_pid=None,
        mailbox=False,
    )
    assert cli_arm._cmd_arm(ns, arm_runner=arm_once, async_runner=run_once) == 0
    assert captured["mailbox"] is False
    assert captured["mailbox_cursor_path"] is None


async def test_arm_passes_mailbox_and_cursor_to_each_wait(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    cursor = tmp_path / "cursor"

    async def wait_once(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 4  # a takeover ends the loop after one wait

    code = await cli_arm._arm(
        uri="ws://h",
        name="B-rx",
        for_name="B",
        reconnect_delay=0.0,
        wait_runner=wait_once,
        mailbox=True,
        mailbox_cursor_path=cursor,
    )
    assert code == 0
    assert captured["mailbox"] is True
    assert captured["mailbox_cursor_path"] == cursor


def test_add_parser_registers_the_mailbox_flag() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    cli_arm.add_parser(sub)
    armed = parser.parse_args(["arm", "--name", "proj/agent", "--mailbox", "--max-wakes", "1"])
    assert armed.mailbox is True
    assert armed.func is cli_arm._cmd_arm
    plain = parser.parse_args(["arm", "--name", "X"])
    assert plain.mailbox is False
    opted_out = parser.parse_args(["arm", "--name", "X", "--no-mailbox"])
    assert opted_out.mailbox is False
