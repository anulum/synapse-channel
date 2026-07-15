# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — isolated editor process-group supervision
"""Terminate a real editor together with every helper in its process group."""

from __future__ import annotations

import os
import signal
import subprocess  # nosec B404
import time

_TERM_TIMEOUT_SECONDS = 15.0
_KILL_TIMEOUT_SECONDS = 5.0
_POLL_INTERVAL_SECONDS = 0.1
PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS = _TERM_TIMEOUT_SECONDS + _KILL_TIMEOUT_SECONDS


def _process_group_exists(process_group: int) -> bool:
    """Return whether an isolated process group still has a member."""
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_process_group_exit(
    process: subprocess.Popen[str],
    process_group: int,
    timeout: float,
    poll_interval: float,
) -> bool:
    """Reap the group leader while waiting for every helper to exit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process.poll()
        if not _process_group_exists(process_group):
            return True
        time.sleep(poll_interval)
    process.poll()
    return not _process_group_exists(process_group)


def terminate_isolated_process_group(
    process: subprocess.Popen[str],
    *,
    term_timeout: float = _TERM_TIMEOUT_SECONDS,
    kill_timeout: float = _KILL_TIMEOUT_SECONDS,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
) -> None:
    """Terminate a session leader and all editor helpers in its process group.

    Parameters
    ----------
    process:
        Editor process started with ``start_new_session=True`` so its PID is
        also the process-group identifier.
    term_timeout:
        Seconds allowed for the complete group to exit after ``SIGTERM``.
    kill_timeout:
        Seconds allowed after escalation to ``SIGKILL``.
    poll_interval:
        Delay between bounded process-group probes.

    Raises
    ------
    ValueError
        If a timeout is negative or the polling interval is not positive.
    RuntimeError
        If the isolated group survives escalation or its leader cannot be
        reaped.
    """
    if term_timeout < 0 or kill_timeout < 0:
        raise ValueError("process-group timeouts must not be negative")
    if poll_interval <= 0:
        raise ValueError("process-group poll interval must be positive")

    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        try:
            process.wait(timeout=0)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("editor process group vanished before its leader exited") from exc
        return
    if not _wait_for_process_group_exit(
        process,
        process_group,
        term_timeout,
        poll_interval,
    ):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if not _wait_for_process_group_exit(
            process,
            process_group,
            kill_timeout,
            poll_interval,
        ):
            raise RuntimeError("isolated editor process group did not exit")
    try:
        process.wait(timeout=0)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("editor process-group leader could not be reaped") from exc
