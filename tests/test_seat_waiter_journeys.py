# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — journeys: a held seat that wakes its own pane, in one process
"""Real tmux, a real hub and a real ``synapse seat-hold --wake-session`` in the pane.

The agent is a small stand-in with Claude's idle prompt, so the waiter can
recognise an idle pane and deliver a wake into it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.agent_tmux import AgentTmuxConfig, start_session
from synapse_channel.seat_hold import marker_path
from synapse_channel.seat_restore import wait_for_marker
from synapse_channel.waiter_identity import pane_waiter_name

IDENTITY = "T/hold"
SESSION = "synapse-T_hold"
NONCE = "journey-0001"

_AGENT = """#!{python}
import pathlib, sys
received = pathlib.Path({received!r})
print("AGENT-OUTPUT")
print("Claude Code")
print()
print("\\u276f ", end="", flush=True)
for line in sys.stdin:
    with received.open("a", encoding="utf-8") as out:
        out.write(line)
    if line.strip() == "quit":
        raise SystemExit(5)
    print("\\u276f ", end="", flush=True)
"""


@pytest.fixture
def private_tmux(tmp_path: Path) -> Iterator[str]:
    """A tmux wrapper on a private socket with no user configuration."""
    if not sys.platform.startswith("linux") or shutil.which("tmux") is None:
        pytest.skip("the seat holder needs Linux and tmux")
    socket = f"synapse-test-{uuid.uuid4().hex[:10]}"
    wrapper = tmp_path / "tmux-private"
    wrapper.write_text(f'#!/bin/sh\nexec tmux -L {socket} -f /dev/null "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    try:
        yield str(wrapper)
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True, check=False)
        socket_dir = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
        (socket_dir / socket).unlink(missing_ok=True)


def _synapse(tmp_path: Path) -> str:
    """``synapse`` for the pane: this checkout, the test machine key, a private runtime."""
    wrapper = tmp_path / "synapse"
    data_home = shlex.quote(os.environ["XDG_DATA_HOME"])
    runtime = shlex.quote(str(tmp_path / "runtime"))
    (tmp_path / "runtime").mkdir(mode=0o700, exist_ok=True)
    wrapper.write_text(
        f"#!/bin/sh\nXDG_DATA_HOME={data_home} XDG_RUNTIME_DIR={runtime} exec {sys.executable} -c "
        "'from synapse_channel.cli import main; raise SystemExit(main())' \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return str(wrapper)


def _agent(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    path.write_text(
        _AGENT.format(python=sys.executable, received=str(tmp_path / "received.txt")),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


PANE = f"={SESSION}:"
"""The session's pane: an exact session name needs the trailing ``:`` in a pane target."""


def _capture(tmux: str) -> str:
    shown = subprocess.run([tmux, "capture-pane", "-p", "-t", PANE], capture_output=True, text=True)
    assert shown.returncode == 0, shown.stderr
    return shown.stdout


def _seat_processes() -> list[str]:
    # -ww: never cut a command line to the 80 columns ps assumes for a pipe
    listing = subprocess.run(["ps", "-ww", "-eo", "args"], capture_output=True, text=True).stdout
    return [line for line in listing.splitlines() if IDENTITY in line and "ps -eo" not in line]


async def _until(check: Callable[[], bool], within: float = 20.0) -> None:
    deadline = time.monotonic() + within
    while not check():
        assert time.monotonic() < deadline, "the condition did not hold in time"
        await asyncio.sleep(0.1)


async def _start(tmp_path: Path, tmux: str, uri: str) -> None:
    synapse = _synapse(tmp_path)
    pane = [
        synapse,
        "seat-hold",
        "--identity",
        IDENTITY,
        "--nonce",
        NONCE,
        "--marker-dir",
        str(tmp_path / "markers"),
        "--uri",
        uri,
        "--wake-session",
        SESSION,
        "--tmux-bin",
        tmux,
        "--pane-probe-interval",
        "0.5",
        "--",
        str(_agent(tmp_path)),
    ]
    config = AgentTmuxConfig(
        identity=IDENTITY,
        session=SESSION,
        cwd=tmp_path,
        agent_command=tuple(pane),
        tmux_bin=tmux,
        synapse_bin=synapse,
        uri=uri,
    )
    started = await asyncio.to_thread(start_session, config)
    assert started.started, started.detail
    marker = marker_path(tmp_path / "markers", IDENTITY, NONCE)
    assert await asyncio.to_thread(wait_for_marker, marker) == "ready"


async def test_a_held_seat_wakes_its_own_pane_in_one_process_and_keeps_it_quiet(
    private_tmux: str, tmp_path: Path
) -> None:
    received = tmp_path / "received.txt"
    async with running_hub() as (hub, uri):
        await _start(tmp_path, private_tmux, uri)
        try:
            await _until(lambda: pane_waiter_name(IDENTITY) in hub.online_agents())
            assert f"{IDENTITY}-seat" in hub.online_agents()
            # One Synapse process for the seat: no `agent-tmux wait`, no `synapse wait` child.
            processes = _seat_processes()
            assert len([line for line in processes if "seat-hold" in line]) == 1, processes
            assert not [line for line in processes if " wait " in f" {line} "], processes
            sender = await connect_agent("SENDER", uri)
            try:
                await sender.agent.chat("please wake", target=IDENTITY)
                await _until(
                    lambda: received.exists() and received.read_text(encoding="utf-8") != ""
                )
            finally:
                await close_agents(sender)
            screen = _capture(private_tmux)
            assert "AGENT-OUTPUT" in screen  # the agent's own output still reaches the pane
            assert "synapse" not in screen.lower() and "waiter" not in screen.lower(), screen
            subprocess.run([private_tmux, "send-keys", "-t", PANE, "quit", "Enter"], check=True)
            await _until(
                lambda: (
                    subprocess.run(
                        [private_tmux, "has-session", "-t", f"={SESSION}"], capture_output=True
                    ).returncode
                    != 0
                )
            )
            await _until(
                lambda: (
                    not {f"{IDENTITY}-seat", pane_waiter_name(IDENTITY)} & set(hub.online_agents())
                )
            )
        finally:
            subprocess.run([private_tmux, "kill-session", "-t", f"={SESSION}"], capture_output=True)


async def test_a_waiter_that_stops_leaves_the_seat_running_and_says_why_off_screen(
    private_tmux: str, tmp_path: Path
) -> None:
    async with running_hub() as (hub, uri):
        await _start(tmp_path, private_tmux, uri)
        try:
            await _until(lambda: pane_waiter_name(IDENTITY) in hub.online_agents())
            # A binding that no longer matches is a lasting refusal: the loop returns.
            subprocess.run(
                [private_tmux, "set-environment", "-t", f"={SESSION}", "SYN_IDENTITY", "T/other"],
                check=True,
            )
            await _until(lambda: pane_waiter_name(IDENTITY) not in hub.online_agents())
            registry = tmp_path / "runtime" / "synapse-agent-tmux"
            await _until(
                lambda: any(
                    "waiter_stopped" in json.loads(path.read_text(encoding="utf-8"))
                    for path in registry.glob("*.json")
                )
            )
            log = (tmp_path / "markers" / "T_hold-holder.log").read_text(encoding="utf-8")
            assert "the pane waiter of T/hold stopped" in log
            assert f"{IDENTITY}-seat" in hub.online_agents()  # the seat is still held
            assert (
                subprocess.run(
                    [private_tmux, "has-session", "-t", f"={SESSION}"], capture_output=True
                ).returncode
                == 0
            )  # and its agent still runs
            assert "waiter" not in _capture(private_tmux).lower()
        finally:
            subprocess.run([private_tmux, "kill-session", "-t", f"={SESSION}"], capture_output=True)
