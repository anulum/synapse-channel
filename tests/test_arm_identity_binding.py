# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — waiter identity binding: explicit flags beat ambient env

"""An explicitly named waiter binds to that identity, whatever the env says.

The 2026-07-10 P0 incident: a session env carrying another seat's
``SYN_IDENTITY``/``SYN_TMUX_PROVIDER`` caused explicitly-named waiters to
either filter for the wrong identity or refuse to arm at all — directed
messages were then lost from the live path while broadcasts kept flowing.
This surface pins the contract at the real CLI dispatch level against a
live hub: ambient environment NEVER overrides an explicit ``--name``/
``--for``, and provider-session markers suppress only the ambient
identity's arm, never an explicitly different one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli_arm
from synapse_channel.core.hub import SynapseHub

AMBIENT = "user/terminal-999"
EXPLICIT = "PROJ/agent-x"


async def _wait_for_presence(observer: AgentHandle, name: str) -> None:
    await observer.recorder.wait_for(
        lambda _message: any(
            item.get("type") == "presence_update" and item.get("agent") == name
            for item in observer.recorder.messages
        )
    )


async def _send_chat(uri: str, sender: str, target: str, payload: str) -> None:
    handle = await connect_agent(sender, uri)
    try:
        await handle.agent.chat(payload, target=target)
    finally:
        await close_agents(handle)


def _parse_arm(argv: list[str]) -> Any:
    """Parse ``arm`` argv through the real registered parser."""
    import argparse

    parser = argparse.ArgumentParser()
    cli_arm.add_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["arm", *argv])


def _ambient_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Poison the environment exactly as the incident session was poisoned."""
    monkeypatch.setenv("SYN_IDENTITY", AMBIENT)
    monkeypatch.setenv("SYN_PROJECT", "user")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))


async def test_an_explicitly_named_waiter_wakes_on_its_own_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # F1 pin: ambient SYN_IDENTITY names ANOTHER seat, yet the waiter armed
    # with an explicit --name must wake on messages to that explicit name.
    _ambient_env(monkeypatch, tmp_path)
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        args = _parse_arm(["--uri", uri, "--name", EXPLICIT, "--directed-only", "--max-wakes", "1"])
        assert (args.for_name or args.name) == EXPLICIT

        arm_task = asyncio.create_task(
            cli_arm._arm(
                uri=uri,
                name=f"{EXPLICIT}-rx",
                for_name=args.for_name or args.name,
                directed_only=True,
                max_wakes=1,
                reconnect_delay=0.0,
            )
        )
        try:
            await _wait_for_presence(observer, f"{EXPLICIT}-rx")
            await _send_chat(uri, "peer", EXPLICIT, "direct wake for the explicit seat")
            code = await asyncio.wait_for(arm_task, timeout=5.0)
        finally:
            if not arm_task.done():
                arm_task.cancel()
            await close_agents(observer)

    assert code == 0
    assert "direct wake for the explicit seat" in capsys.readouterr().out


async def test_a_message_to_the_ambient_identity_does_not_wake_the_explicit_waiter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    # The inverse guard: the explicit waiter must NOT be listening on the
    # ambient identity's stream (that mis-binding was the incident).
    _ambient_env(monkeypatch, tmp_path)
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        arm_task = asyncio.create_task(
            cli_arm._arm(
                uri=uri,
                name=f"{EXPLICIT}-rx",
                for_name=EXPLICIT,
                directed_only=True,
                max_wakes=1,
                reconnect_delay=0.0,
            )
        )
        try:
            await _wait_for_presence(observer, f"{EXPLICIT}-rx")
            await _send_chat(uri, "peer", AMBIENT, "for the ambient seat only")
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(arm_task), timeout=1.0)
        finally:
            arm_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await arm_task
            await close_agents(observer)


def test_provider_env_flag_must_not_suppress_an_explicitly_named_arm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # F2: SYN_TMUX_PROVIDER=1 marks the SESSION as provider-backed. That may
    # suppress an arm for the AMBIENT identity (the provider is its waker),
    # but an arm explicitly requesting a DIFFERENT identity must proceed —
    # the provider wakes nobody for that seat.
    _ambient_env(monkeypatch, tmp_path)
    monkeypatch.setenv("SYN_TMUX_PROVIDER", "1")
    captured: dict[str, Any] = {}

    async def arm_capture(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    args = _parse_arm(["--name", EXPLICIT, "--directed-only", "--max-wakes", "1"])
    code = cli_arm._cmd_arm(args, arm_runner=arm_capture)

    out = capsys.readouterr().out
    assert code == 0
    assert captured.get("for_name") == EXPLICIT, (
        f"explicit arm was suppressed instead of armed: {out!r}"
    )


def test_provider_env_flag_still_suppresses_the_ambient_identitys_arm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The wake-loop containment stays: an inner provider-session agent arming
    # its OWN (ambient) identity keeps yielding to the pane bridge.
    _ambient_env(monkeypatch, tmp_path)
    monkeypatch.setenv("SYN_TMUX_PROVIDER", "1")
    called: dict[str, Any] = {}

    async def arm_capture(**kwargs: Any) -> int:
        called.update(kwargs)
        return 0

    args = _parse_arm(["--name", AMBIENT, "--directed-only", "--max-wakes", "1"])
    code = cli_arm._cmd_arm(args, arm_runner=arm_capture)

    assert code == 0
    assert not called, "ambient-identity arm must yield to the provider"
    assert "provider" in capsys.readouterr().out.lower()


def test_a_foreign_provider_pidfile_must_not_suppress_an_explicit_arm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    # F2, pidfile flavour: a LIVE provider pidfile for the ambient identity
    # exists, but the arm names a different seat — it must proceed.
    import os

    _ambient_env(monkeypatch, tmp_path)
    runtime = tmp_path / "synapse-provider-tmux"
    runtime.mkdir()
    (runtime / "user_terminal-999.pid").write_text(str(os.getpid()))
    captured: dict[str, Any] = {}

    async def arm_capture(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    args = _parse_arm(["--name", EXPLICIT, "--directed-only", "--max-wakes", "1"])
    code = cli_arm._cmd_arm(args, arm_runner=arm_capture)

    assert code == 0
    assert captured.get("for_name") == EXPLICIT


def test_an_unreadable_provider_pidfile_never_suppresses_an_arm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    # A corrupt pidfile for the REQUESTED identity is treated as no provider:
    # the probe fails safe towards arming (reachability), never towards a
    # silent dark seat.
    from synapse_channel.shell_integration import has_active_tmux_provider

    _ambient_env(monkeypatch, tmp_path)
    runtime = tmp_path / "synapse-provider-tmux"
    runtime.mkdir()
    (runtime / "PROJ_agent-x.pid").write_text("not-a-pid")

    assert has_active_tmux_provider(EXPLICIT) is False
