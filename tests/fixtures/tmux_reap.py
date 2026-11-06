# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stop a private tmux server together with its pane programs
"""Stop a test-owned tmux server and every program its panes started.

``tmux kill-server`` only sends SIGHUP to each pane. A pane program that
ignores SIGHUP keeps running after the server is gone, now a child of init, and
holds memory until someone notices. Each tmux pane runs in a session of its
own, so the programs it started stay findable by that session id; this module
kills them after the server has stopped, and removes the socket file the
stopped server leaves behind.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

PROC = Path("/proc")


def pane_sessions(base: Sequence[str]) -> frozenset[int]:
    """Return the session ids of every pane of the tmux server ``base`` names.

    Parameters
    ----------
    base : Sequence[str]
        The tmux command and its private-socket arguments (``tmux -L name``).

    Returns
    -------
    frozenset[int]
        One session id per pane; empty when the server is not running.
    """
    listed = subprocess.run(
        [*base, "list-panes", "-a", "-F", "#{pane_pid}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if listed.returncode != 0:
        return frozenset()
    return frozenset(int(pid) for pid in listed.stdout.split() if pid.isdigit())


def _socket_path(base: Sequence[str]) -> Path | None:
    """Return the socket file of the running server ``base`` names, or ``None``.

    The server does not remove its socket file when ``kill-server`` stops it.
    """
    shown = subprocess.run(
        [*base, "display-message", "-p", "#{socket_path}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    path = shown.stdout.strip()
    return Path(path) if shown.returncode == 0 and path else None


def processes_in_sessions(sessions: frozenset[int], proc: Path = PROC) -> list[int]:
    """Return the live processes whose session id is one of ``sessions``.

    Zombies are left out: they hold no memory and cannot be killed, only reaped
    by their parent.
    """
    found: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        # The command name may hold spaces or parentheses; fields start after the last ')'.
        state, _ppid, _pgrp, session = stat[stat.rindex(")") + 2 :].split()[:4]
        if state != "Z" and int(session) in sessions:
            found.append(int(entry.name))
    return found


def stop_private_tmux_server(
    base: Sequence[str], *, timeout: float = 5.0, proc: Path = PROC
) -> None:
    """Kill the tmux server ``base`` names, every program left in its panes, and its socket.

    Parameters
    ----------
    base : Sequence[str]
        The tmux command and its private-socket arguments (``tmux -L name``).
    timeout : float, optional
        Seconds to wait for the killed pane programs to disappear.
    proc : pathlib.Path, optional
        The procfs root to read process sessions from.

    Raises
    ------
    AssertionError
        When a pane program is still alive after ``timeout`` seconds.
    """
    sessions = pane_sessions(base)
    socket = _socket_path(base)
    subprocess.run([*base, "kill-server"], capture_output=True, check=False, timeout=10)
    if socket is not None:
        socket.unlink(missing_ok=True)
    deadline = time.monotonic() + timeout
    while alive := processes_in_sessions(sessions, proc):
        if time.monotonic() > deadline:
            raise AssertionError(f"pane programs of a private tmux server survived: {alive}")
        for pid in alive:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.05)
