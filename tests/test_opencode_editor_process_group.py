# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — isolated editor process-group supervision tests
"""Exercise bounded cleanup for real editor processes and their helpers."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from e2e.opencode_editors.process_group import terminate_isolated_process_group


def _assert_process_group_gone(process_group: int) -> None:
    """Accept ESRCH or Darwin EPERM once the reaped group is no longer signalable."""
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    raise AssertionError(f"process group {process_group} is still signalable")


class _FakeProcess:
    """Minimal typed process double for deterministic group cleanup tests."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        """Return the configured process status."""
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """Return a completed status or model a live process timeout."""
        if self.returncode is None:
            raise subprocess.TimeoutExpired("editor", timeout or 0.0)
        return self.returncode


def _popen(process: _FakeProcess) -> subprocess.Popen[str]:
    """Present the minimal process double through the production protocol."""
    return cast(subprocess.Popen[str], process)


def _wait_for_marker(path: Path, process: subprocess.Popen[str]) -> None:
    """Wait until the isolated process publishes a complete helper PID."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            payload = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            payload = ""
        if payload.isdigit() and int(payload) > 0:
            return
        if process.poll() is not None:
            raise AssertionError("editor fixture exited before starting its helper")
        time.sleep(0.01)
    raise AssertionError("editor fixture did not start its helper")


def _isolated_editor_process(marker: Path, *, ignore_term: bool) -> subprocess.Popen[str]:
    """Start a real session leader with one helper in the same process group."""
    signal_setup = "signal.signal(signal.SIGTERM, signal.SIG_IGN);" if ignore_term else ""
    child = f"import signal,time;{signal_setup}time.sleep(60)"
    parent = (
        "import pathlib,signal,subprocess,sys,time;"
        f"{signal_setup}"
        f"child=subprocess.Popen([sys.executable,'-c',{child!r}]);"
        f"pathlib.Path({str(marker)!r}).write_text(str(child.pid),encoding='utf-8');"
        "time.sleep(60)"
    )
    return subprocess.Popen(  # nosec B603
        [sys.executable, "-c", parent],
        start_new_session=True,
        text=True,
    )


def test_cleanup_terminates_a_real_editor_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "helper.pid"
    process = _isolated_editor_process(marker, ignore_term=False)
    _wait_for_marker(marker, process)

    terminate_isolated_process_group(process, poll_interval=0.01)

    assert marker.read_text(encoding="utf-8").isdigit()
    assert process.returncode == -signal.SIGTERM
    _assert_process_group_gone(process.pid)


def test_cleanup_kills_a_real_group_that_ignores_sigterm(tmp_path: Path) -> None:
    marker = tmp_path / "helper.pid"
    process = _isolated_editor_process(marker, ignore_term=True)
    _wait_for_marker(marker, process)

    terminate_isolated_process_group(
        process,
        term_timeout=0.05,
        kill_timeout=5.0,
        poll_interval=0.01,
    )

    assert process.returncode == -signal.SIGKILL
    _assert_process_group_gone(process.pid)


def test_cleanup_terminates_the_complete_isolated_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(321)
    group_alive = True
    signals: list[int] = []

    def killpg(process_group: int, requested_signal: int) -> None:
        nonlocal group_alive
        assert process_group == process.pid
        if requested_signal == 0:
            if not group_alive:
                raise ProcessLookupError
            return
        signals.append(requested_signal)
        if requested_signal == signal.SIGTERM:
            group_alive = False
            process.returncode = -15

    monkeypatch.setattr(os, "killpg", killpg)

    terminate_isolated_process_group(_popen(process))

    assert signals == [signal.SIGTERM]


def test_cleanup_escalates_a_stuck_group(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess(654)
    group_alive = True
    signals: list[int] = []

    def killpg(process_group: int, requested_signal: int) -> None:
        nonlocal group_alive
        assert process_group == process.pid
        if requested_signal == 0:
            if not group_alive:
                raise ProcessLookupError
            return
        signals.append(requested_signal)
        if requested_signal == signal.SIGKILL:
            group_alive = False
            process.returncode = -9

    monkeypatch.setattr(os, "killpg", killpg)

    terminate_isolated_process_group(_popen(process), term_timeout=0.0)

    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_cleanup_waits_for_delayed_helper_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess(700)
    probes = 0

    def killpg(_process_group: int, requested_signal: int) -> None:
        nonlocal probes
        if requested_signal != 0:
            return
        probes += 1
        if probes >= 2:
            process.returncode = -15
            raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", killpg)

    terminate_isolated_process_group(_popen(process), poll_interval=0.001)

    assert probes == 2


def test_cleanup_accepts_an_already_exited_group(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess(777)
    process.returncode = 0

    def missing_group(_process_group: int, _requested_signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", missing_group)

    terminate_isolated_process_group(_popen(process))


def test_cleanup_refuses_a_live_leader_without_its_expected_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(778)

    def missing_group(_process_group: int, _requested_signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", missing_group)

    with pytest.raises(RuntimeError, match="vanished before its leader exited"):
        terminate_isolated_process_group(_popen(process))


def test_cleanup_handles_a_group_that_exits_before_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(779)
    group_alive = True

    def killpg(_process_group: int, requested_signal: int) -> None:
        nonlocal group_alive
        if requested_signal == signal.SIGKILL:
            group_alive = False
            process.returncode = -9
            raise ProcessLookupError
        if requested_signal == 0 and not group_alive:
            raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", killpg)

    terminate_isolated_process_group(_popen(process), term_timeout=0.0)


def test_cleanup_treats_permission_probe_as_leader_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Darwin may raise EPERM on killpg(0); fall back to the leader poll."""
    process = _FakeProcess(790)
    signals: list[int] = []

    def killpg(_process_group: int, requested_signal: int) -> None:
        if requested_signal == 0:
            raise PermissionError
        signals.append(requested_signal)
        if requested_signal == signal.SIGKILL:
            process.returncode = -9

    monkeypatch.setattr(os, "killpg", killpg)

    terminate_isolated_process_group(_popen(process), term_timeout=0.0, poll_interval=0.001)

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.returncode == -9


def test_cleanup_refuses_a_group_that_survives_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess(780)
    monkeypatch.setattr(os, "killpg", lambda _group, _signal: None)

    with pytest.raises(RuntimeError, match="process group did not exit"):
        terminate_isolated_process_group(
            _popen(process),
            term_timeout=0.0,
            kill_timeout=0.0,
        )


def test_cleanup_refuses_an_unreapable_group_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(781)
    group_alive = True

    def killpg(_process_group: int, requested_signal: int) -> None:
        nonlocal group_alive
        if requested_signal == signal.SIGTERM:
            group_alive = False
        elif requested_signal == 0 and not group_alive:
            raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", killpg)

    with pytest.raises(RuntimeError, match="leader could not be reaped"):
        terminate_isolated_process_group(_popen(process))


@pytest.mark.parametrize(
    ("term_timeout", "kill_timeout", "poll_interval", "message"),
    [
        (-1.0, 1.0, 0.1, "timeouts"),
        (1.0, -1.0, 0.1, "timeouts"),
        (1.0, 1.0, 0.0, "poll interval"),
    ],
)
def test_cleanup_refuses_invalid_bounds(
    term_timeout: float,
    kill_timeout: float,
    poll_interval: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        terminate_isolated_process_group(
            _popen(_FakeProcess(888)),
            term_timeout=term_timeout,
            kill_timeout=kill_timeout,
            poll_interval=poll_interval,
        )
