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

import pytest

_config: pytest.Config | None = None
_stderr_fd = -1
_active: dict[str, tuple[threading.Event, threading.Thread]] = {}


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


def pytest_configure(config: pytest.Config) -> None:
    """Keep the config and a stderr fd that stays valid after capture starts."""
    global _config, _stderr_fd
    _config = config
    stream = sys.__stderr__
    if stream is None:
        return
    try:
        _stderr_fd = os.dup(stream.fileno())
    except (AttributeError, OSError, ValueError):
        _stderr_fd = -1


def pytest_unconfigure(config: pytest.Config) -> None:
    """Close the stderr duplicate opened for hang reports."""
    del config
    global _stderr_fd
    if _stderr_fd >= 0:
        os.close(_stderr_fd)
        _stderr_fd = -1


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    """Start the hang watch for the test that is about to run."""
    del location
    limit = _limit()
    if limit <= 0:
        return
    finished = threading.Event()

    def _report(bound: float = limit, stop: threading.Event = finished) -> None:
        # Sleep in short slices. Event.wait holds a condition lock, and
        # faulthandler's watchdog thread dumps every thread when the timeout
        # fires. On macOS, dumping a thread parked on that lock and then
        # joining it from logfinish leaves cancel_dump_traceback_later waiting
        # for the dumper, so the pytest process never exits.
        deadline = time.monotonic() + bound
        while time.monotonic() < deadline:
            if stop.is_set():
                return
            remaining = deadline - time.monotonic()
            time.sleep(min(0.05, remaining))
        if stop.is_set():
            return
        _write_timeout(nodeid)

    watcher = threading.Thread(target=_report, name="pytest-hang-identity", daemon=True)
    watcher.start()
    _active[nodeid] = (finished, watcher)


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    """Stop the hang watch after the test, including its teardown, has finished.

    The watcher is a daemon and is not joined. Joining it while the
    faulthandler watchdog is dumping that thread deadlocks on macOS.
    """
    del location
    pair = _active.pop(nodeid, None)
    if pair is None:
        return
    pair[0].set()
