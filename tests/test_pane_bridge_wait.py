# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — one long bridge wait, stopped as soon as its target is lost
"""Drive real child processes through the probed bridge wait."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence

import pytest

from synapse_channel.pane_bridge_wait import (
    DEFAULT_BRIDGE_WAIT_TIMEOUT,
    WaitProcess,
    run_probed_wait,
    stop_wait,
)

_SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


class _Recorder:
    """Real :class:`subprocess.Popen` spawner that keeps the started child for checks."""

    def __init__(self) -> None:
        self.children: list[subprocess.Popen[str]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        stdout: int,
        stderr: int,
        text: bool,
        env: Mapping[str, str],
    ) -> WaitProcess:
        child = subprocess.Popen(list(args), stdout=stdout, stderr=stderr, text=text, env=dict(env))
        self.children.append(child)
        return child


def test_a_wait_that_ends_returns_its_output_without_probing() -> None:
    """A delivered wake comes back as the child's exit code and output."""
    probes: list[int] = []

    def probe() -> int:
        probes.append(1)
        return 0

    command = [sys.executable, "-c", "print('woke')"]
    finished, refusal = run_probed_wait(command, env=os.environ, interval=30.0, probe=probe)
    assert (finished.returncode, finished.stdout, refusal) == (0, "woke\n", 0)
    assert finished.args == command
    assert probes == []
    assert DEFAULT_BRIDGE_WAIT_TIMEOUT >= 60


def test_a_refused_probe_stops_the_wait_and_reports_the_refusal() -> None:
    """The child keeps running while the probe passes and is stopped when it refuses."""
    answers = iter([0, 0, 7])
    heartbeats: list[int] = []
    spawner = _Recorder()
    finished, refusal = run_probed_wait(
        _SLEEPER,
        env=os.environ,
        interval=0.1,
        probe=lambda: next(answers),
        heartbeat=lambda: heartbeats.append(1),
        spawner=spawner,
    )
    assert (finished.returncode, finished.stdout, finished.stderr, refusal) == (2, "", "", 7)
    assert len(heartbeats) == 3
    (child,) = spawner.children
    assert child.poll() is not None


def test_a_failing_probe_still_stops_the_wait() -> None:
    """An error inside the probe never leaves the child connected."""
    spawner = _Recorder()

    def probe() -> int:
        raise RuntimeError("probe broke")

    with pytest.raises(RuntimeError, match="probe broke"):
        run_probed_wait(_SLEEPER, env=os.environ, interval=0.1, probe=probe, spawner=spawner)
    (child,) = spawner.children
    assert child.poll() is not None


def test_stopping_kills_a_child_that_ignores_sigterm_and_reaps_a_finished_one() -> None:
    """``SIGTERM`` first, ``SIGKILL`` after the grace period; an exited child is only reaped."""
    # The context managers close each child's pipes once it is stopped.
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as stubborn:
        assert stubborn.stdout is not None
        assert stubborn.stdout.readline() == "ready\n"
        assert stop_wait(stubborn, grace=0.2) == -9
    with subprocess.Popen(
        [sys.executable, "-c", "raise SystemExit(5)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as finished:
        finished.wait(timeout=20)
        assert stop_wait(finished) == 5
