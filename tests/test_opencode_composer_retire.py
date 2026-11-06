# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a retired native startup leaves no client running
"""Retiring an unpublished native startup stops the programs its pane started.

``tmux kill-session`` only sends SIGHUP to the pane. A native client that
ignores it kept running under init after a refused launch; such clients were
found running for days. These tests use real processes and a private tmux.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from fixtures.tmux_reap import pane_sessions, processes_in_sessions, stop_private_tmux_server
from synapse_channel.agent_tmux import AgentTmuxConfig
from synapse_channel.opencode_composer import LAUNCH_ENV
from synapse_channel.opencode_composer_startup import (
    _pane_groups,
    _retire_owned_startup,
    end_process_groups,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("tmux") is None,
    reason="process groups are checked through Linux procfs and need tmux",
)

HUP_PROOF = "trap '' HUP; exec sleep 600"
STUBBORN = "trap '' HUP TERM; while :; do sleep 1; done"
NONCE = "0123456789abcdef0123456789abcdef"


def _detached_group(program: str) -> int:
    """Start ``program`` as a detached process group leader and return its pid.

    Like a client left behind by ``kill-session``, it is not this process's
    child: init reaps it, so an ended group really disappears.
    """
    started = subprocess.run(
        ["sh", "-c", f"setsid sh -c {shlex.quote(program)} >/dev/null 2>&1 & echo $!"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(started.stdout)


def _gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def groups() -> Iterator[list[int]]:
    """Every detached group a test starts; any survivor is killed afterwards."""
    started: list[int] = []
    yield started
    for group in started:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)


def test_a_group_that_ignores_sighup_ends_on_sigterm(groups: list[int]) -> None:
    """The ordinary case: SIGTERM ends the client well inside the grace period."""
    groups.append(client := _detached_group(HUP_PROOF))
    time.sleep(0.2)  # let the shell install its trap before the signal
    started = time.monotonic()
    end_process_groups([client], grace=2.0)
    assert _gone(client, timeout=1.0)
    assert time.monotonic() - started < 1.5


def test_a_group_that_ignores_sigterm_too_is_killed_after_the_grace(groups: list[int]) -> None:
    """A client that ignores SIGTERM is killed once the grace period is over."""
    groups.append(client := _detached_group(STUBBORN))
    time.sleep(0.2)  # let the shell install its traps before the signal
    started = time.monotonic()
    end_process_groups([client], grace=0.3)
    assert _gone(client)
    assert 0.3 <= time.monotonic() - started < 2.0


def test_a_group_that_is_already_gone_is_skipped(groups: list[int]) -> None:
    """No group, nothing to signal and no wait."""
    groups.append(finished := _detached_group("exit 0"))
    assert _gone(finished)
    started = time.monotonic()
    end_process_groups([finished])
    assert time.monotonic() - started < 0.2


def test_the_callers_own_group_and_init_are_never_signalled() -> None:
    """Group 0 and the caller's own group would end the caller; 1 is init.

    The call runs in a child that leads its own group, so a regression ends
    only that child and fails here instead of stopping the whole test run.
    """
    probe = (
        "import os, time\n"
        "from synapse_channel.opencode_composer_startup import end_process_groups\n"
        "started = time.monotonic()\n"
        "end_process_groups([0, 1, os.getpgrp()])\n"
        "print(f'alive {time.monotonic() - started:.3f}')\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
        start_new_session=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert child.returncode == 0, (child.returncode, child.stderr)
    label, elapsed = child.stdout.split()
    assert label == "alive" and float(elapsed) < 0.2


def _foreign_group_leader() -> int | None:
    """Return a process group this user may not signal, or ``None`` if there is none.

    The probe is signal 0, which checks permission and delivers nothing.
    """
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) <= 1:
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        pgrp = int(stat[stat.rindex(")") + 2 :].split()[2])
        if pgrp != int(entry.name):
            continue
        try:
            os.killpg(pgrp, 0)
        except PermissionError:
            return pgrp
        except ProcessLookupError:
            continue
    return None


FOREIGN_GROUP = _foreign_group_leader() if sys.platform.startswith("linux") else None


@pytest.mark.skipif(
    FOREIGN_GROUP is None, reason="no process group here that this user may not signal"
)
def test_a_group_of_another_user_is_left_alone_without_an_error() -> None:
    """A recycled group id that now belongs to someone else is neither waited for nor raised.

    The kernel refuses the signal (PermissionError), so nothing reaches that group.
    """
    assert FOREIGN_GROUP is not None
    started = time.monotonic()
    end_process_groups([FOREIGN_GROUP])
    assert time.monotonic() - started < 0.2
    assert Path(f"/proc/{FOREIGN_GROUP}").exists()


class _Failing:
    """A command runner whose tmux exchange fails, as a vanished session does."""

    def __call__(self, args: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 1, "", "no such session")


def _config(tmux_bin: str, root: Path) -> AgentTmuxConfig:
    return AgentTmuxConfig("E2E/retire", "agent", root, tmux_bin=tmux_bin)


def test_panes_of_a_vanished_session_have_no_groups(tmp_path: Path) -> None:
    assert _pane_groups(_config("tmux", tmp_path), "gone", _Failing()) == []


@pytest.fixture
def private_tmux(tmp_path: Path) -> Iterator[tuple[tuple[str, ...], str, dict[int, int | None]]]:
    """A private tmux socket, the wrapper the startup code calls as its tmux, and its panes.

    Tests record each pane session when they start it, so the last-resort
    cleanup still finds pane programs after the code under test ended the
    session itself.
    """
    tmux = shutil.which("tmux")
    assert tmux is not None
    base = (tmux, "-L", "synapse-retire-" + uuid.uuid4().hex, "-f", "/dev/null")
    wrapper = tmp_path / "tmux"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.join(base) + ' "$@"\n', encoding="utf-8")
    wrapper.chmod(0o700)
    sessions: dict[int, int | None] = {}
    try:
        yield base, str(wrapper), sessions
    finally:
        stop_private_tmux_server(base)
        for pid in processes_in_sessions(sessions):
            os.kill(pid, signal.SIGKILL)


def test_retiring_the_owned_startup_stops_the_client_it_launched(
    private_tmux: tuple[tuple[str, ...], str, dict[int, int | None]], tmp_path: Path
) -> None:
    """The session is gone and so is the client that ignored the hang-up."""
    base, wrapper, sessions = private_tmux
    target = "synapse-native-start-" + NONCE
    subprocess.run([*base, "new-session", "-d", "-s", "anchor", "sleep 600"], check=True)
    subprocess.run(
        [*base, "new-session", "-d", "-s", target, "-e", f"{LAUNCH_ENV}={NONCE}"]
        + ["sh", "-c", HUP_PROOF],
        check=True,
    )
    sessions.update(pane_sessions(base))
    listed = subprocess.run(
        [*base, "list-panes", "-t", target, "-F", "#{pane_pid}"],
        capture_output=True,
        text=True,
        check=True,
    )
    (client,) = (int(pid) for pid in listed.stdout.split())

    _retire_owned_startup(_config(wrapper, tmp_path), target, NONCE, subprocess.run)

    remaining = subprocess.run(
        [*base, "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True
    )
    assert remaining.stdout.split() == ["anchor"]
    assert processes_in_sessions({client: None}) == []
    with pytest.raises(ProcessLookupError):
        os.kill(client, 0)
