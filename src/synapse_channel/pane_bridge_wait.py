# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one long pane-bridge wait, probed while it runs
"""Keep one ``synapse wait`` connected while the pane it serves is probed.

A pane bridge used to start a new ``synapse wait`` process every probe interval
so that its receiver presence could never outlive the pane. That costs a Python
start and a hub reconnect per bridge every few seconds. Here one wait stays
connected for a long timeout while the caller's pane probe runs every interval;
the moment the probe refuses, the wait is stopped, so the hub drops the receiver
within the same bound as before.
"""

from __future__ import annotations

# The wait child is this module's controlled process boundary.
import subprocess  # nosec B404
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

DEFAULT_BRIDGE_WAIT_TIMEOUT = 300.0
"""Seconds one bridge wait stays connected before it is restarted after a fresh probe."""

DEFAULT_TERMINATE_GRACE = 2.0
"""Seconds a stopped wait gets to exit after ``SIGTERM`` before ``SIGKILL``."""


class WaitProcess(Protocol):
    """The part of :class:`subprocess.Popen` that a probed wait uses."""

    returncode: int | None

    def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
        """Wait for exit, returning standard output and error."""
        ...

    def poll(self) -> int | None:
        """Return the exit code, or ``None`` while running."""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """Wait for exit and return the exit code."""
        ...

    def terminate(self) -> None:
        """Send ``SIGTERM``."""
        ...

    def kill(self) -> None:
        """Send ``SIGKILL``."""
        ...


class WaitSpawner(Protocol):
    """Callable compatible with :class:`subprocess.Popen` for the wait child."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        stdout: int,
        stderr: int,
        text: bool,
        env: Mapping[str, str],
    ) -> WaitProcess:
        """Start the wait child."""
        ...


def _popen(
    args: Sequence[str], *, stdout: int, stderr: int, text: bool, env: Mapping[str, str]
) -> WaitProcess:
    """Start ``args`` with :class:`subprocess.Popen`."""
    # The command is the fixed `synapse wait` argument vector built by the bridge.
    return subprocess.Popen(  # nosec B603
        list(args), stdout=stdout, stderr=stderr, text=text, env=dict(env)
    )


def stop_wait(process: WaitProcess, *, grace: float = DEFAULT_TERMINATE_GRACE) -> int:
    """Stop a running wait: ``SIGTERM``, a bounded wait, then ``SIGKILL``; always reap it.

    Parameters
    ----------
    process : WaitProcess
        The wait child.
    grace : float, optional
        Seconds to wait after ``SIGTERM`` before ``SIGKILL``.

    Returns
    -------
    int
        The child's exit status once reaped.
    """
    if process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
    return process.wait()


def run_probed_wait(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    interval: float,
    probe: Callable[[], int],
    heartbeat: Callable[[], None] | None = None,
    spawner: WaitSpawner = _popen,
) -> tuple[subprocess.CompletedProcess[str], int]:
    """Run one wait child while ``probe`` re-proves its target every ``interval`` seconds.

    Parameters
    ----------
    command : Sequence[str]
        The ``synapse wait`` argument vector.
    env : Mapping[str, str]
        Environment of the wait child.
    interval : float
        Seconds between probes; it bounds how long a lost target keeps a receiver.
    probe : Callable[[], int]
        Returns ``0`` while the target is valid, otherwise a refusal exit code.
    heartbeat : Callable[[], None] or None, optional
        Called after every probe interval, so a supervisor sees the loop advance
        during a long wait.
    spawner : WaitSpawner, optional
        Starts the child; injectable for tests.

    Returns
    -------
    tuple[subprocess.CompletedProcess[str], int]
        The finished wait and ``0``, or a timeout-shaped result (exit ``2``, no
        output) and the probe's refusal code when the target was lost and the
        wait was stopped.
    """
    process = spawner(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=max(interval, 0.1))
            except subprocess.TimeoutExpired:
                if heartbeat is not None:
                    heartbeat()
                refusal = probe()
                if refusal:
                    stop_wait(process)
                    return subprocess.CompletedProcess(command, 2, "", ""), refusal
                continue
            return subprocess.CompletedProcess(command, process.wait(), stdout, stderr), 0
    finally:
        if process.poll() is None:
            stop_wait(process)
