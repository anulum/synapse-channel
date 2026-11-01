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


def _watch(stop: threading.Event, deadline: float, nodeid: str) -> None:
    """Wait until ``deadline`` and then name ``nodeid``, unless ``stop`` is set.

    Short sleeps keep the watcher off ``Event.wait``. Parking there is only a
    suspected interaction with faulthandler's watchdog on macOS; it was not
    reproduced with a macOS sample. The old ``join(timeout=2)`` cannot explain
    a 20 second overrun, because that join returns after two seconds.
    """
    while True:
        if stop.is_set():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.05, remaining))
    _write_timeout(nodeid)


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    """Start the hang watch for the test that is about to run."""
    del location
    limit = _limit()
    if limit <= 0:
        return
    finished = threading.Event()
    current = nodeid

    def _report(
        bound: float = limit,
        stop: threading.Event = finished,
        name: str = current,
    ) -> None:
        _watch(stop, time.monotonic() + bound, name)

    watcher = threading.Thread(target=_report, name="pytest-hang-identity", daemon=True)
    watcher.start()
    _active[nodeid] = (finished, watcher)


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    """Stop the hang watch after the test, including its teardown, has finished.

    The watcher is a daemon and is not joined. The previous join waited at
    most two seconds, so leaving it out does not by itself explain the macOS
    overrun. Not joining still lets the process exit without waiting for the
    daemon.
    """
    del location
    pair = _active.pop(nodeid, None)
    if pair is None:
        return
    pair[0].set()
