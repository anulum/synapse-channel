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
import ctypes
import os
import sys
import time
from pathlib import Path

import pytest

from synapse_channel.cli_lock_process import _terminate_locked_command, run_locked_subprocess

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
_NOTE_SIGNAL_AND_SLEEP = (
    "import os, pathlib, signal, sys, time\n"
    "def _noted(signum, frame):\n"
    "    pathlib.Path(sys.argv[2]).write_text('signalled', encoding='utf-8')\n"
    "signal.signal(signal.SIGTERM, _noted)\n"
    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
    "time.sleep(30)\n"
)

_WAIT_FOR_A_FILE_THEN_ANSWER = (
    "import pathlib, sys, time\n"
    "while not pathlib.Path(sys.argv[1]).exists():\n"
    "    time.sleep(0.01)\n"
    "pathlib.Path(sys.argv[2]).write_text('answered', encoding='utf-8')\n"
)
_LEAVE_A_DESCENDANT_AND_END = (
    "import subprocess, sys\n"
    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]])\n"
)


def _assert_reaped(pid: int) -> None:
    """The recorded child is no longer a live process.

    ``os.kill(pid, 0)`` is the POSIX existence probe. Windows rejects signal 0
    whether or not the process exists, so the probe there reads the exit status.
    """
    if sys.platform != "win32":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.GetExitCodeProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.OpenProcess(0x1000, False, pid)
    assert handle
    status = ctypes.c_uint32()
    assert kernel.GetExitCodeProcess(handle, ctypes.byref(status))
    kernel.CloseHandle(handle)
    assert status.value != 259


def _assert_absent(pid: int) -> None:
    """The process id no longer names a live process.

    A Windows process that has already been reaped cannot be opened, and that
    refusal is the same fact as a POSIX ``ProcessLookupError``. An open process
    is absent only when its exit code is no longer active.
    """
    if sys.platform != "win32":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.GetExitCodeProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        assert ctypes.get_last_error() == 87
        return
    status = ctypes.c_uint32()
    assert kernel.GetExitCodeProcess(handle, ctypes.byref(status))
    kernel.CloseHandle(handle)
    assert status.value != 259


def _still_alive(pid: int) -> bool:
    """Return whether a POSIX process id still names a process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _reap_task() -> asyncio.Task[None]:
    """Return the reap the locked command started for this cancellation."""
    matches = [task for task in asyncio.all_tasks() if task.get_name() == "locked-command-reap"]
    assert matches, "the locked command had not started reaping"
    return matches[0]


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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals the process group, then waits")
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
    assert time.monotonic() - started >= 4.5
    _assert_reaped(int(pid_file.read_text(encoding="utf-8")))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals the process group")
async def test_a_command_that_already_ended_is_not_signalled(tmp_path: Path) -> None:
    """A finished command's group is left alone, so a descendant it left behind still answers."""
    release = tmp_path / "release"
    answered = tmp_path / "answered"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _LEAVE_A_DESCENDANT_AND_END,
        _WAIT_FOR_A_FILE_THEN_ANSWER,
        str(release),
        str(answered),
        start_new_session=True,
    )
    try:
        assert await proc.wait() == 0
        await _terminate_locked_command(proc)
    finally:
        release.write_text("go", encoding="utf-8")
    deadline = time.monotonic() + 20
    while not answered.exists():
        assert time.monotonic() < deadline, "the descendant did not answer after the helper"
        await asyncio.sleep(0.01)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows terminates the direct child once")
async def test_windows_termination_reaps_the_command(tmp_path: Path) -> None:
    """Cancellation on Windows terminates the direct child and reaps it."""
    pid_file = tmp_path / "pid"
    command = [sys.executable, "-c", _RECORD_AND_SLEEP, str(pid_file)]
    locked = asyncio.create_task(run_locked_subprocess(command))
    await _wait_until_started(pid_file)
    locked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await locked
    _assert_reaped(int(pid_file.read_text(encoding="utf-8")))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX waits out the grace period")
async def test_an_interrupt_during_the_reap_still_reaps_the_command(tmp_path: Path) -> None:
    """An interrupt while the command is signalled, and still alive, still reaps it."""
    pid_file = tmp_path / "pid"
    signalled = tmp_path / "signalled"
    command = [sys.executable, "-c", _NOTE_SIGNAL_AND_SLEEP, str(pid_file), str(signalled)]
    locked = asyncio.create_task(run_locked_subprocess(command))
    await _wait_until_started(pid_file)
    pid = int(pid_file.read_text(encoding="utf-8"))
    locked.cancel()
    deadline = time.monotonic() + 8
    while not (signalled.is_file() and _still_alive(pid)):
        assert time.monotonic() < deadline, "the command was not signalled while it was still alive"
        await asyncio.sleep(0.01)
    _reap_task().cancel()
    with pytest.raises(asyncio.CancelledError):
        await locked
    _assert_reaped(pid)
    assert _still_alive(pid) is False
