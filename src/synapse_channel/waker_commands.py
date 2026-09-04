# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded service command processes
"""Bound service-command waits without claiming that systemd jobs were cancelled."""

from __future__ import annotations

import contextlib
import math
import os
import signal
import subprocess  # nosec B404

DEFAULT_COMMAND_TIMEOUT = 30.0
"""Maximum seconds spent waiting for one service command, excluding process creation."""


def command_timeout(value: float) -> float:
    """Validate a positive finite command wait in seconds.

    Raises
    ------
    ValueError
        The bound is boolean, non-finite or non-positive.
    """
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("command timeout must be positive and finite")
    return float(value)


def run_waker_command(
    args: list[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    check: bool = False,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run a captured text command, terminating its group and reaping its direct child.

    Parameters
    ----------
    args : list of str
        Fixed command arguments, executed without a shell.
    capture_output, text : bool
        Both must be true; this runner serves the waker service interface.
    check : bool
        Raise CalledProcessError on a non-zero exit when true.
    timeout : float
        Positive finite wait in seconds; process creation is outside this bound.

    Returns
    -------
    subprocess.CompletedProcess
        Captured text and the process exit code.

    Raises
    ------
    subprocess.TimeoutExpired
        The wait expired. The local process group is killed and its leader reaped; a job
        already accepted by systemd may still run.
    ValueError
        Capture mode or timeout is invalid.
    OSError
        Process creation or termination failed.
    """
    limit = command_timeout(timeout)
    if not capture_output or not text:
        raise ValueError("waker commands require captured text output")
    with subprocess.Popen(  # nosec B603
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=limit)
        except BaseException:
            # The group is ours even if its leader has exited with pipes held by a child.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
    result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result
