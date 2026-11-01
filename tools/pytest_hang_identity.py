# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — name the test that outlives the faulthandler timeout
"""Print the running pytest node id when ``faulthandler_timeout`` fires.

The standard dump names threads and frames only. A hung shard then cannot say
which test was running. This watcher uses the same timeout and writes one
``TIMEOUT nodeid=`` line. Pytest still prints its own traceback.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass

import pytest

_config: pytest.Config | None = None
_stderr_fd = -1
_arm_lock = threading.Lock()
_session_stop = threading.Event()
_session_thread: threading.Thread | None = None
# Bound at import. Tests may replace time.monotonic and time.sleep; the
# watcher must keep the real clock and must not consume a scripted one.
_monotonic = time.monotonic
_pause = threading.Event()


@dataclass
class _Arm:
    """One test's deadline. The session watcher is re-armed, not replaced."""

    nodeid: str
    deadline: float
    stopped: bool = False
    reported: bool = False


_arm: _Arm | None = None


def _write_timeout(nodeid: str) -> None:
    """Write the hung node id to the stderr captured when pytest configured."""
    if _stderr_fd < 0:
        return
    os.write(_stderr_fd, f"TIMEOUT nodeid={nodeid}\n".encode())


def _limit() -> float:
    """Return the per-test faulthandler timeout as of this moment."""
    if _config is None:
        return 0.0
    raw = _config.getini("faulthandler_timeout")
    try:
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _take_action(now: float) -> tuple[str, str]:
    """Decide the session watcher's next step and mark a due test reported.

    Returns ``halt``, ``idle``, ``wait``, or ``report`` plus the node id to
    write. The caller sleeps outside this lock. One session thread is re-armed
    per test so a finished test does not leave a thread behind.
    """
    with _arm_lock:
        if _session_stop.is_set():
            return ("halt", "")
        arm = _arm
        if arm is None:
            return ("idle", "")
        if arm.stopped:
            return ("idle", "")
        if arm.reported:
            return ("idle", "")
        if now >= arm.deadline:
            arm.reported = True
            return ("report", arm.nodeid)
        return ("wait", "")


def _session_loop() -> None:
    """Re-arm the same thread for every test until the session stops."""
    while True:
        action, nodeid = _take_action(_monotonic())
        if action == "halt":
            return
        if action == "report":
            _write_timeout(nodeid)
            continue
        _pause.wait(0.05)
        _pause.clear()


def _ensure_session_watcher() -> None:
    """Start the single daemon watcher if the session does not already have one."""
    global _session_thread
    if _session_thread is not None and _session_thread.is_alive():
        return
    _session_stop.clear()
    _pause.clear()
    _session_thread = threading.Thread(
        target=_session_loop,
        name="pytest-hang-identity",
        daemon=True,
    )
    _session_thread.start()


def _join_session_thread() -> None:
    """Wait for the session watcher if one is recorded. A missing thread is idle."""
    global _session_thread
    thread = _session_thread
    _session_thread = None
    if thread is not None:
        thread.join(timeout=1)


def _stop_session_watcher() -> None:
    """Ask the session watcher to exit and wait briefly. It is a daemon."""
    _session_stop.set()
    _pause.set()
    _join_session_thread()


def pytest_configure(config: pytest.Config) -> None:
    """Keep the config, a stderr fd, and the one session watcher."""
    global _config, _stderr_fd
    _config = config
    stream = sys.__stderr__
    if stream is not None:
        try:
            _stderr_fd = os.dup(stream.fileno())
        except (AttributeError, OSError, ValueError):
            _stderr_fd = -1
    _ensure_session_watcher()


def pytest_unconfigure(config: pytest.Config) -> None:
    """Stop the session watcher and close the stderr duplicate."""
    del config
    global _stderr_fd
    _stop_session_watcher()
    if _stderr_fd >= 0:
        os.close(_stderr_fd)
        _stderr_fd = -1


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    """Re-arm the session watcher for the test that is about to run."""
    del location
    limit = _limit()
    if limit <= 0:
        return
    _ensure_session_watcher()
    with _arm_lock:
        global _arm
        _arm = _Arm(nodeid, _monotonic() + limit)


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    """Disarm the session watcher. The thread itself stays for the next test."""
    del location
    with _arm_lock:
        if _arm is not None and _arm.nodeid == nodeid:
            _arm.stopped = True
