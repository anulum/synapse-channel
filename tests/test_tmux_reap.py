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
from pathlib import Path

import pytest

from fixtures.tmux_reap import pane_sessions, processes_in_sessions, stop_private_tmux_server

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("tmux") is None,
    reason="pane sessions are read from Linux procfs and need tmux",
)

HUP_PROOF = "trap '' HUP; exec sleep 600"


@pytest.fixture
def base() -> Iterator[tuple[str, ...]]:
    """A private tmux socket, with a last-resort kill of anything a failed test left."""
    tmux = shutil.which("tmux")
    assert tmux is not None
    command = (tmux, "-L", "synapse-reap-" + uuid.uuid4().hex, "-f", "/dev/null")
    yield command
    sessions = pane_sessions(command)
    subprocess.run([*command, "kill-server"], capture_output=True, check=False, timeout=10)
    for pid in processes_in_sessions(sessions):
        os.kill(pid, signal.SIGKILL)
    _socket(command).unlink(missing_ok=True)


def _socket(base: tuple[str, ...]) -> Path:
    """The socket file tmux uses for the private server ``base`` names."""
    return Path(f"/tmp/tmux-{os.getuid()}") / base[base.index("-L") + 1]


def _start(base: tuple[str, ...], name: str, program: str) -> None:
    subprocess.run(
        [*base, "new-session", "-d", "-s", name, "sh", "-c", program],
        capture_output=True,
        check=True,
        timeout=10,
    )


def _wait_for(sessions: frozenset[int], count: int) -> list[int]:
    deadline = time.monotonic() + 5
    while len(found := processes_in_sessions(sessions)) < count:
        assert time.monotonic() < deadline, f"expected {count} pane programs, found {found}"
        time.sleep(0.05)
    return found


def test_a_pane_program_that_ignores_sighup_is_stopped_with_the_server(
    base: tuple[str, ...],
) -> None:
    """The server and every pane program are gone; nothing outside the panes is touched."""
    _start(base, "anchor", "exec sleep 600")
    _start(base, "client", HUP_PROOF)
    sessions = pane_sessions(base)
    assert len(sessions) == 2
    started = _wait_for(sessions, 2)
    bystander = subprocess.Popen(["sleep", "600"])
    socket = _socket(base)
    assert socket.exists()
    try:
        stop_private_tmux_server(base)

        assert processes_in_sessions(sessions) == []
        for pid in started:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        listed = subprocess.run([*base, "list-sessions"], capture_output=True, check=False)
        assert listed.returncode != 0
        assert not socket.exists()
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(timeout=5)


def test_kill_server_alone_leaves_the_sighup_proof_program_running(
    base: tuple[str, ...],
) -> None:
    """The gap the helper closes: ``kill-server`` only hangs up on the panes."""
    _start(base, "client", HUP_PROOF)
    sessions = pane_sessions(base)
    (survivor,) = _wait_for(sessions, 1)
    subprocess.run([*base, "kill-server"], capture_output=True, check=False, timeout=10)
    time.sleep(0.3)
    try:
        assert processes_in_sessions(sessions) == [survivor]
        assert _socket(base).exists()
    finally:
        os.kill(survivor, signal.SIGKILL)


def test_a_server_that_is_not_running_is_a_no_op(base: tuple[str, ...]) -> None:
    """No server, no panes: stopping returns at once and finds nothing to kill."""
    assert pane_sessions(base) == frozenset()
    stop_private_tmux_server(base, timeout=0.1)


def test_a_pane_program_that_cannot_be_stopped_is_reported(
    base: tuple[str, ...], tmp_path: Path
) -> None:
    """A survivor past the deadline fails the teardown loudly instead of leaking quietly."""
    _start(base, "client", "exec sleep 600")
    (pane,) = pane_sessions(base)
    # A procfs view in which a process of that pane's session never goes away.
    stat = tmp_path / "proc" / "999999999" / "stat"
    stat.parent.mkdir(parents=True)
    stat.write_text(f"999999999 (stuck) S 1 {pane} {pane} 0 0", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"survived: \[999999999\]"):
        stop_private_tmux_server(base, timeout=0.2, proc=tmp_path / "proc")


def test_procfs_reading_skips_zombies_odd_names_and_unreadable_entries(tmp_path: Path) -> None:
    """Names with spaces and parentheses parse; zombies and entries without stat do not count."""
    stat = tmp_path / "proc" / "424242" / "stat"
    stat.parent.mkdir(parents=True)
    stat.write_text("424242 (odd (name) x) S 1 424242 7 0 0", encoding="utf-8")
    (tmp_path / "proc" / "self").mkdir()
    zombie = tmp_path / "proc" / "424243" / "stat"
    zombie.parent.mkdir()
    zombie.write_text("424243 (gone) Z 1 424243 7 0 0", encoding="utf-8")
    (tmp_path / "proc" / "424244").mkdir()
    assert processes_in_sessions(frozenset({7}), proc=tmp_path / "proc") == [424242]
