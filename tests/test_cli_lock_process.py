# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — locked command launch, refusal, and force-kill
"""The lock command's subprocess helper finishes, refuses, or force-kills."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from synapse_channel.cli_lock_process import run_locked_subprocess

_RECORD_AND_SLEEP = (
    "import os, pathlib, sys, time\n"
    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
    "time.sleep(30)\n"
)
_IGNORE_TERMINATION = (
    "import os, pathlib, signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
    "time.sleep(30)\n"
)


def _assert_reaped(pid: int) -> None:
    """The recorded child is no longer a live process."""
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def _wait_until_started(pid_file: Path) -> None:
    """Wait until the child has recorded the process id the test will reap."""
    deadline = time.monotonic() + 10
    while not pid_file.exists() or not pid_file.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline, "the locked command did not start"
        await asyncio.sleep(0.01)


async def test_a_finished_command_returns_its_status() -> None:
    """A command that exits is reaped and its status is returned."""
    code = await run_locked_subprocess([sys.executable, "-c", "import sys; raise SystemExit(3)"])
    assert code == 3


async def test_a_missing_command_names_the_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    """A command the host cannot find is reported and returns 127."""
    missing = "/no/such/synapse-lock-command"
    code = await run_locked_subprocess([missing])
    assert code == 127
    captured = capsys.readouterr()
    assert f"lock: cannot execute {missing!r}" in captured.err
    assert captured.out == ""


async def test_a_directory_command_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory cannot be executed, so the helper returns 126."""
    code = await run_locked_subprocess([str(tmp_path)])
    assert code == 126
    assert "lock: cannot execute" in capsys.readouterr().err


async def test_a_command_that_ignores_termination_is_killed(tmp_path: Path) -> None:
    """Cancellation waits out the grace period, then force-kills the command."""
    pid_file = tmp_path / "pid"
    command = [sys.executable, "-c", _IGNORE_TERMINATION, str(pid_file)]
    locked = asyncio.create_task(run_locked_subprocess(command))
    await _wait_until_started(pid_file)
    started = time.monotonic()
    locked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await locked
    elapsed = time.monotonic() - started
    assert elapsed >= 4.5
    assert elapsed < 15
    _assert_reaped(int(pid_file.read_text(encoding="utf-8")))


async def test_windows_termination_reaps_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows branch terminates the direct child and reaps it."""
    monkeypatch.setattr(os, "name", "nt")
    pid_file = tmp_path / "pid"
    command = [sys.executable, "-c", _RECORD_AND_SLEEP, str(pid_file)]
    locked = asyncio.create_task(run_locked_subprocess(command))
    await _wait_until_started(pid_file)
    locked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await locked
    _assert_reaped(int(pid_file.read_text(encoding="utf-8")))


async def test_windows_kill_reaps_a_command_that_ignores_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows child that survives termination is force-killed after the grace period."""
    monkeypatch.setattr(os, "name", "nt")
    pid_file = tmp_path / "pid"
    command = [sys.executable, "-c", _IGNORE_TERMINATION, str(pid_file)]
    locked = asyncio.create_task(run_locked_subprocess(command))
    await _wait_until_started(pid_file)
    started = time.monotonic()
    locked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await locked
    assert time.monotonic() - started >= 4.5
    _assert_reaped(int(pid_file.read_text(encoding="utf-8")))


async def test_an_interrupt_during_the_reap_still_reaps_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupt delivered to the reap wait is ignored until the command is dead."""
    original = asyncio.subprocess.Process.wait
    seen = {"calls": 0}

    async def wait(self: asyncio.subprocess.Process) -> int:
        """Cancel the cleanup once, on the reap that follows the grace wait."""
        seen["calls"] += 1
        if seen["calls"] == 3:
            current = asyncio.current_task()
            assert current is not None
            current.cancel()
        return await original(self)

    monkeypatch.setattr(asyncio.subprocess.Process, "wait", wait)
    pid_file = tmp_path / "pid"
    command = [sys.executable, "-c", _IGNORE_TERMINATION, str(pid_file)]
    locked = asyncio.create_task(run_locked_subprocess(command))
    await _wait_until_started(pid_file)
    locked.cancel()
    await asyncio.sleep(0)
    cleanup = asyncio.all_tasks() - {locked, asyncio.current_task()}
    assert len(cleanup) == 1
    await asyncio.sleep(0.05)
    cleanup.pop().cancel()
    with pytest.raises(asyncio.CancelledError):
        await locked
    assert seen["calls"] >= 3
    _assert_reaped(int(pid_file.read_text(encoding="utf-8")))
