# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a private tmux server leaves no pane program behind
"""Stopping a test-owned tmux server stops the programs its panes started.

A pane program that ignores SIGHUP survives ``tmux kill-server``. Native
composer acceptance runs a real client in such panes, and copies of it were
found still running days after their test runs ended.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from fixtures.tmux_reap import (
    pane_sessions,
    processes_in_sessions,
    socket_path,
    stop_private_tmux_server,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("tmux") is None,
    reason="pane sessions are read from Linux procfs and need tmux",
)

HUP_PROOF = "trap '' HUP; exec sleep 600"


@dataclass
class Rig:
    """A private tmux socket and every pane session and socket file started on it."""

    base: tuple[str, ...]
    sessions: dict[int, int | None] = field(default_factory=dict)
    sockets: set[Path] = field(default_factory=set)

    def start(self, name: str, program: str) -> None:
        """Start a session running ``program`` and record its pane and socket."""
        subprocess.run(
            [*self.base, "new-session", "-d", "-s", name, "sh", "-c", program],
            capture_output=True,
            check=True,
            timeout=10,
        )
        self.sessions.update(pane_sessions(self.base))
        socket = socket_path(self.base)
        assert socket is not None
        self.sockets.add(socket)


@pytest.fixture
def rig() -> Iterator[Rig]:
    """A private tmux socket, with a last-resort kill of anything a failed test left.

    The sessions were recorded when they started, so this still finds pane
    programs after the server itself is gone.
    """
    tmux = shutil.which("tmux")
    assert tmux is not None
    owned = Rig((tmux, "-L", "synapse-reap-" + uuid.uuid4().hex, "-f", "/dev/null"))
    yield owned
    subprocess.run([*owned.base, "kill-server"], capture_output=True, check=False, timeout=10)
    for pid in processes_in_sessions(owned.sessions):
        os.kill(pid, signal.SIGKILL)
    for socket in owned.sockets:
        socket.unlink(missing_ok=True)


def _wait_for(sessions: dict[int, int | None], count: int) -> list[int]:
    deadline = time.monotonic() + 5
    while len(found := processes_in_sessions(sessions)) < count:
        assert time.monotonic() < deadline, f"expected {count} pane programs, found {found}"
        time.sleep(0.05)
    return found


def _stat_line(pid: int, state: str, session: int, started: int) -> str:
    """A procfs stat line with a command name holding spaces and parentheses."""
    rest = [state, "1", str(pid), str(session), *["0"] * 15, str(started), "0"]
    return f"{pid} (odd (name) x) " + " ".join(rest)


def _fake_proc(root: Path, *processes: tuple[int, str, int, int]) -> Path:
    for pid, state, session, started in processes:
        (root / str(pid)).mkdir(parents=True)
        (root / str(pid) / "stat").write_text(_stat_line(pid, state, session, started))
    return root


def test_a_pane_program_that_ignores_sighup_is_stopped_with_the_server(rig: Rig) -> None:
    """The server and every pane program are gone; nothing outside the panes is touched."""
    rig.start("anchor", "exec sleep 600")
    rig.start("client", HUP_PROOF)
    sessions = pane_sessions(rig.base)
    assert len(sessions) == 2 and None not in sessions.values()
    started = _wait_for(sessions, 2)
    bystander = subprocess.Popen(["sleep", "600"])
    (socket,) = rig.sockets
    assert socket.exists()
    try:
        stop_private_tmux_server(rig.base)

        assert processes_in_sessions(sessions) == []
        for pid in started:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        listed = subprocess.run([*rig.base, "list-sessions"], capture_output=True, check=False)
        assert listed.returncode != 0
        assert not socket.exists()
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(timeout=5)


def test_kill_server_alone_leaves_the_sighup_proof_program_running(rig: Rig) -> None:
    """The gap the helper closes: ``kill-server`` only hangs up on the panes."""
    rig.start("client", HUP_PROOF)
    (survivor,) = _wait_for(rig.sessions, 1)
    subprocess.run([*rig.base, "kill-server"], capture_output=True, check=False, timeout=10)
    time.sleep(0.3)
    assert processes_in_sessions(rig.sessions) == [survivor]
    assert all(socket.exists() for socket in rig.sockets)


def test_a_server_that_is_not_running_is_a_no_op(rig: Rig) -> None:
    """No server, no panes, no socket: stopping returns at once and finds nothing."""
    assert pane_sessions(rig.base) == {}
    assert socket_path(rig.base) is None
    stop_private_tmux_server(rig.base, timeout=0.1)


def test_a_pane_program_that_cannot_be_stopped_is_reported(rig: Rig, tmp_path: Path) -> None:
    """A survivor past the deadline fails the teardown loudly instead of leaking quietly."""
    rig.start("client", "exec sleep 600")
    (pane,) = rig.sessions
    # A procfs view in which a process of that pane's session never goes away.
    proc = _fake_proc(tmp_path / "proc", (999999999, "S", pane, 5))
    with pytest.raises(AssertionError, match=r"survived: \[999999999\]"):
        stop_private_tmux_server(rig.base, timeout=0.2, proc=proc)


def test_a_recycled_session_leader_pid_is_not_taken_for_the_pane(tmp_path: Path) -> None:
    """A new process holding a gone leader's pid, started later, leads someone else's session."""
    proc = _fake_proc(tmp_path / "proc", (7, "S", 7, 200), (8, "S", 7, 210))
    assert processes_in_sessions({7: 200}, proc) == [7, 8]
    assert processes_in_sessions({7: 100}, proc) == []
    assert processes_in_sessions({7: None}, proc) == []


def test_a_session_whose_leader_is_gone_still_yields_its_programs(tmp_path: Path) -> None:
    """With no process holding the leader's pid, the session's remaining programs count."""
    proc = _fake_proc(tmp_path / "proc", (8, "S", 7, 210))
    assert processes_in_sessions({7: 200}, proc) == [8]
    assert processes_in_sessions({7: None}, proc) == [8]


def test_procfs_reading_skips_zombies_and_unreadable_entries(tmp_path: Path) -> None:
    """Zombies, non-process entries and entries without a stat file do not count."""
    proc = _fake_proc(tmp_path / "proc", (424242, "S", 9, 1), (424243, "Z", 9, 1))
    (proc / "self").mkdir()
    (proc / "424244").mkdir()
    assert processes_in_sessions({9: None}, proc) == [424242]
