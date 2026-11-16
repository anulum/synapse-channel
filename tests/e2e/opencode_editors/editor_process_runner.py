# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed real-editor process supervision
"""Run an editor driver and every descendant in one bounded process group."""

from __future__ import annotations

import math
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from e2e.opencode_editors.process_group import (
    PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS,
    terminate_isolated_process_group,
)

PROCESS_GROUP_SUPERVISION_ENV = "SYNAPSE_EDITOR_E2E_PROCESS_GROUP_SUPERVISED"


def _require_bounded_editor_command(
    command: Sequence[str],
    timeout: float,
) -> tuple[str, ...]:
    """Reject a command or timeout that cannot reserve process-group cleanup."""
    argv = tuple(command)
    if not argv or any(not isinstance(argument, str) or not argument for argument in argv):
        raise ValueError("editor command must contain non-empty string arguments")
    if not math.isfinite(timeout) or timeout <= PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS:
        raise ValueError("editor timeout must reserve the complete process-group cleanup budget")
    return argv


def _start_isolated_editor_process(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> subprocess.Popen[str]:
    """Start the editor driver as a new session leader with the supervision marker."""
    process_env = dict(env)
    process_env[PROCESS_GROUP_SUPERVISION_ENV] = "1"
    return subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=process_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _finish_isolated_editor_process(
    process: subprocess.Popen[str],
    argv: tuple[str, ...],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Collect output, clean the group, and report a timeout against the full budget."""
    command_timeout = timeout - PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS
    timed_out: subprocess.TimeoutExpired | None = None
    stdout = ""
    stderr = ""
    try:
        stdout, stderr = process.communicate(timeout=command_timeout)
    except subprocess.TimeoutExpired as exc:
        timed_out = exc
    finally:
        terminate_isolated_process_group(process)
        stdout, stderr = process.communicate()

    if timed_out is not None:
        raise subprocess.TimeoutExpired(
            list(argv),
            timeout,
            output=stdout,
            stderr=stderr,
        ) from timed_out
    returncode = cast(int, process.returncode)
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def run_isolated_editor_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run one editor driver with an end-to-end process-group deadline.

    The command becomes a new session leader. Editors, ACP proxies, and helper
    processes inherit that group unless they deliberately detach. Cleanup time
    is reserved before the command budget is calculated, and the complete group
    is terminated after success, failure, or timeout.

    Parameters
    ----------
    command:
        Non-empty executable and argument sequence.
    cwd:
        Working directory for the editor journey.
    env:
        Complete child environment. The runner adds a supervision marker used
        by drivers that refuse unsupervised execution.
    timeout:
        Total seconds for command execution plus process-group cleanup.

    Returns
    -------
    subprocess.CompletedProcess[str]
        Captured output and the driver's terminal status.

    Raises
    ------
    ValueError
        If the command or timeout cannot provide a bounded cleanup window.
    subprocess.TimeoutExpired
        If the driver exceeds its reserved command budget. The group is fully
        cleaned before the exception is raised.
    RuntimeError
        If process-group cleanup cannot prove that every member exited.
    """
    argv = _require_bounded_editor_command(command, timeout)
    process = _start_isolated_editor_process(argv, cwd=cwd, env=env)
    return _finish_isolated_editor_process(process, argv, timeout)
