# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — isolated editor process-group supervision
"""Terminate a real editor together with every helper in its process group."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404
import time

_TERM_TIMEOUT_SECONDS = 15.0
_KILL_TIMEOUT_SECONDS = 5.0
_POLL_INTERVAL_SECONDS = 0.1
PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS = _TERM_TIMEOUT_SECONDS + _KILL_TIMEOUT_SECONDS


def _process_group_exists(
    process_group: int,
    *,
    leader: subprocess.Popen[str],
) -> bool:
    """Return whether an isolated process group still has a live member.

    ``os.killpg(..., 0)`` is the portable group probe. On Darwin runners (and
    some sandboxes) that probe can raise :class:`PermissionError` even for a
    group this process created. When that happens, fall back to the group
    leader's reaped state: a reaped leader means cleanup has finished for our
    purposes; a live leader means the group is still present.
    """
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return leader.poll() is None
    return True


def _signal_group_or_leader(
    process_group: int,
    sig: signal.Signals,
    leader: subprocess.Popen[str],
) -> None:
    """Signal the process group, falling back to the leader on permission refusal."""
    try:
        os.killpg(process_group, sig)
    except ProcessLookupError:
        raise
    except PermissionError:
        with contextlib.suppress(ProcessLookupError):
            if sig == signal.SIGTERM:
                leader.terminate()
            elif sig == signal.SIGKILL:
                leader.kill()


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
        if not _process_group_exists(process_group, leader=process):
            return True
        time.sleep(poll_interval)
    process.poll()
    return not _process_group_exists(process_group, leader=process)


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
        _signal_group_or_leader(process_group, signal.SIGTERM, process)
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
        with contextlib.suppress(ProcessLookupError):
            _signal_group_or_leader(process_group, signal.SIGKILL, process)
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
