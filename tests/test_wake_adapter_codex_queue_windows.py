# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native-queue wake through a Windows command
"""Start the queue command as a suspended job, then let it answer.

The shared stand-in is a shebang script. CreateProcess does not run that.
This test uses a ``.cmd`` Windows can start.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from synapse_channel.wake_adapters import ACCEPTED, REFUSED, UNCERTAIN, WakeTarget
from synapse_channel.wake_adapters.codex_queue import PROVES, CodexQueueAdapter

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="a suspended job object is a Windows process",
)

_SESSION = "01a0fcac-3407-71c3-9081-0bff345ef6c2"
_HOST = "this-host"
_TARGET = WakeTarget("PROJECT/codex-aaaa", _SESSION, _HOST)


def test_a_cmd_queue_is_owned_and_accepted(tmp_path: Path) -> None:
    """A command Windows can start is accepted when it prints the queued line."""
    program = tmp_path / "codex.cmd"
    program.write_text(
        "@echo off\r\n"
        "echo Queued message 01a10909-405b-7471-86b2-3a44cd8bf3bc for thread %3\r\n"
        "exit /b 0\r\n",
        encoding="utf-8",
    )
    adapter = CodexQueueAdapter(program, host=_HOST)

    outcome = adapter.wake(_TARGET, 1, "fixed prompt", 20.0)

    assert outcome.state == ACCEPTED
    assert outcome.proves == PROVES
    assert outcome.detail == "queued message 01a10909-405b-7471-86b2-3a44cd8bf3bc"


def _batch(directory: Path, body: str) -> Path:
    """Write a command Windows will start, and return its path."""
    program = directory / "codex.cmd"
    program.write_text(body, encoding="utf-8")
    return program


def _python_batch(directory: Path, script: str) -> Path:
    """Start ``script`` through a batch, so the adapter's argv reaches Python."""
    source = directory / "standin.py"
    source.write_text(script, encoding="utf-8")
    return _batch(
        directory,
        f'@echo off\r\n"{sys.executable}" "{source}" %*\r\n',
    )


def _alive(pid: int) -> bool:
    """Report whether the process is still running, not merely unreaped."""
    if sys.platform != "win32":
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = wintypes.DWORD()
    running = bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    kernel.CloseHandle(handle)
    return running


def _stop(pid: int) -> None:
    """End one process the test itself still owns."""
    if sys.platform != "win32":
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x0001, False, pid)
    if handle:
        kernel.TerminateProcess(handle, 1)
        kernel.CloseHandle(handle)


def _pid(directory: Path, name: str) -> int:
    return int((directory / name).read_text(encoding="ascii"))


def test_a_known_daemon_text_is_refused(tmp_path: Path) -> None:
    """A non-zero exit that carries the daemon's refusal text is refused."""
    program = _batch(
        tmp_path,
        "@echo off\r\necho no rollout found 1>&2\r\nexit /b 1\r\n",
    )
    outcome = CodexQueueAdapter(program, host=_HOST).wake(_TARGET, 1, "fixed", 20.0)
    assert (outcome.state, outcome.proves, outcome.detail) == (
        REFUSED,
        "",
        "the daemon refused (exit status 1)",
    )


def test_zero_without_the_queued_line_and_an_unknown_error_are_uncertain(tmp_path: Path) -> None:
    """Any other completed command is uncertain: the message may already be queued."""
    silent = _batch(tmp_path, "@echo off\r\necho something else\r\nexit /b 0\r\n")
    outcome = CodexQueueAdapter(silent, host=_HOST).wake(_TARGET, 1, "fixed", 20.0)
    assert (outcome.state, outcome.detail) == (
        UNCERTAIN,
        "exit status 0 without the queued line for the session",
    )
    other = tmp_path / "other"
    other.mkdir()
    failed = _batch(other, "@echo off\r\necho other 1>&2\r\nexit /b 3\r\n")
    outcome = CodexQueueAdapter(failed, host=_HOST).wake(_TARGET, 1, "fixed", 20.0)
    assert (outcome.state, outcome.detail) == (
        UNCERTAIN,
        "exit status 3 with an unknown error text",
    )


_LIVE = """\
import subprocess, sys, time
from pathlib import Path
root = Path(__file__).resolve().parent
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
(root / "child.pid").write_text(str(child.pid), encoding="ascii")
(root / "parent.pid").write_text(str(__import__("os").getpid()), encoding="ascii")
time.sleep(60)
"""

_EXITED = """\
import subprocess, sys
from pathlib import Path
root = Path(__file__).resolve().parent
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
(root / "child.pid").write_text(str(child.pid), encoding="ascii")
raise SystemExit(0)
"""

_KEEP = """\
import subprocess, sys
from pathlib import Path
root = Path(__file__).resolve().parent
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
(root / "child.pid").write_text(str(child.pid), encoding="ascii")
print(
    "Queued message 01a10909-405b-7471-86b2-3a44cd8bf3bc for thread " + sys.argv[3],
    flush=True,
)
"""


def test_a_live_parent_and_its_descendant_end_with_the_deadline(tmp_path: Path) -> None:
    """The job ends the command and the child it started when the deadline passes."""
    program = _python_batch(tmp_path, _LIVE)
    adapter = CodexQueueAdapter(program, host=_HOST, end_wait_seconds=1.0)
    started = time.monotonic()
    outcome = adapter.wake(_TARGET, 1, "fixed", 1.0)
    elapsed = time.monotonic() - started
    parent, child = _pid(tmp_path, "parent.pid"), _pid(tmp_path, "child.pid")
    try:
        assert (outcome.state, outcome.detail) == (
            UNCERTAIN,
            "no answer within the time limit; it may be queued",
        )
        assert elapsed < 8.0
        assert not _alive(parent) and not _alive(child)
    finally:
        _stop(parent)
        _stop(child)


def test_an_exited_parent_cannot_leave_its_pipe_descendant_past_the_deadline(
    tmp_path: Path,
) -> None:
    """A child that inherited the pipe is still in the job, so the deadline ends it."""
    program = _python_batch(tmp_path, _EXITED)
    adapter = CodexQueueAdapter(program, host=_HOST, end_wait_seconds=1.0)
    started = time.monotonic()
    outcome = adapter.wake(_TARGET, 1, "fixed", 1.0)
    elapsed = time.monotonic() - started
    child = _pid(tmp_path, "child.pid")
    try:
        assert (outcome.state, outcome.detail) == (
            UNCERTAIN,
            "no answer within the time limit; it may be queued",
        )
        assert elapsed < 8.0
        assert not _alive(child)
    finally:
        _stop(child)


def test_a_successful_command_keeps_its_descendant_until_explicit_cleanup(tmp_path: Path) -> None:
    """Acceptance closes the job handle and does not end what the command started."""
    program = _python_batch(tmp_path, _KEEP)
    outcome = CodexQueueAdapter(program, host=_HOST).wake(_TARGET, 1, "fixed", 20.0)
    child = _pid(tmp_path, "child.pid")
    try:
        assert outcome.state == ACCEPTED
        assert outcome.proves == PROVES
        assert _alive(child)
    finally:
        _stop(child)
        assert not _alive(child)
