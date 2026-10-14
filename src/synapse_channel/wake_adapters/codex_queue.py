# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake adapter that queues the fixed prompt through the Codex daemon
"""Wake a Codex session through its own queue.

The client offers ``codex queue --thread <session> --message <text>``: its
local daemon puts a message into a running session. This adapter runs that
command with the relay's fixed prompt as the text.

The command takes no key by which a repeat could be recognised. So the
adapter is strict about what it calls what:

``accepted``
    Exit status 0 and the line ``Queued message <id> for thread <session>``
    for exactly the session asked. It shows that the daemon queued the
    message, nothing more.
``refused``
    The command was not started, or it ended with a non-zero status and one
    of the configured refusal texts on its error output. The one observed so
    far is the daemon's answer for a session it does not know.
``uncertain``
    Everything else: no answer within the time limit, a zero status without
    the line, a non-zero status with an unknown text. The message may be
    queued.

The session is named by its identifier alone, and only when it is a UUID; a
session *name* is never passed. The command runs from a fixed absolute path,
with an argument vector and no shell.

The adapter owns what the command starts, from before the command runs. On
POSIX the command is started as the leader of a process group of its own. On
Windows it is created suspended, put into a job object and only then resumed.
When that ownership cannot be established, the command is ended before it ran
and the wake is refused.

When the time limit passes, the whole group or job is ended, also when the
command itself has already exited and only a process it started still holds
the output open. The wait for the remaining output is bounded as well, so the
adapter returns even when a descendant left the group or the job. When the
command ends in time, nothing is ended.

Ending the group or job also ends a daemon that this very call started and
that stayed in it; a daemon that was running before is not touched.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import socket
import subprocess  # nosec B404
import sys
import uuid
from pathlib import Path

from synapse_channel.wake_adapters import (
    ACCEPTED,
    REFUSED,
    UNCERTAIN,
    Availability,
    WakeOutcome,
    WakeTarget,
    validate_deadline,
)

KIND = "native_queue"
"""The adapter's name in marks and receipts."""

PROVES = "queued_by_native_daemon"
"""What an accepted wake of this adapter shows."""

DEFAULT_REFUSALS = ("no rollout found",)
"""Error texts that mean the daemon did not queue the message."""

END_WAIT_SECONDS = 5.0
"""Longest wait for the output of a child that was ended at the time limit."""

_QUEUED = re.compile(r"Queued message (\S+) for thread (\S+)")
_MAX_DETAIL = 200


def canonical_session(value: str) -> str | None:
    """Return ``value`` when it is a UUID in its canonical lower-case form, else None.

    Other spellings of a UUID are not accepted: the identifier is passed on
    exactly as the registry holds it, or not at all.
    """
    try:
        parsed = str(uuid.UUID(value))
    except ValueError:
        return None
    return parsed if parsed == value else None


class CodexQueueAdapter:
    """Wake a seat by queueing the fixed prompt into its Codex session."""

    def __init__(
        self,
        executable: Path,
        *,
        host: str | None = None,
        refusals: tuple[str, ...] = DEFAULT_REFUSALS,
        end_wait_seconds: float = END_WAIT_SECONDS,
    ) -> None:
        self._executable = executable
        self._host = socket.gethostname() if host is None else host
        self._refusals = refusals
        self._end_wait = end_wait_seconds

    @property
    def kind(self) -> str:
        """Return ``native_queue``."""
        return KIND

    def available(self, target: WakeTarget) -> Availability:
        """Say whether the exact session can be asked from this host."""
        if canonical_session(target.native_session) is None:
            return Availability(False, "the seat has no native session identifier that is a UUID")
        if target.execution_host != self._host:
            return Availability(False, "the session runs on another host")
        program = self._executable
        if not program.is_absolute() or not program.is_file() or not os.access(program, os.X_OK):
            return Availability(False, "the client's executable is not at its fixed path")
        return Availability(True)

    def wake(
        self, target: WakeTarget, wake_id: int, prompt: str, deadline_seconds: float
    ) -> WakeOutcome:
        """Queue the prompt once and say what the command reported."""
        del wake_id
        try:
            validate_deadline(deadline_seconds)
        except ValueError:
            return WakeOutcome(REFUSED, "", "not started: invalid wake deadline")
        availability = self.available(target)
        if not availability.available:
            return WakeOutcome(REFUSED, "", f"not started: {availability.detail}")
        session = target.native_session
        argv = [str(self._executable), "queue", "--thread", session, "--message", prompt]
        try:
            child, owned = _start_owned(argv)
        except _NoCustody:
            return WakeOutcome(REFUSED, "", "not started: the process tree could not be owned")
        except OSError as exc:
            return WakeOutcome(REFUSED, "", f"not started: {type(exc).__name__}")
        try:
            output, errors = child.communicate(timeout=deadline_seconds)
        except subprocess.TimeoutExpired:
            _end(owned, child, self._end_wait)
            return WakeOutcome(UNCERTAIN, "", "no answer within the time limit; it may be queued")
        finally:
            _disown(owned)
        return self._judge(child.returncode, output, errors, session)

    def _judge(self, status: int, output: str, errors: str, session: str) -> WakeOutcome:
        if status == 0:
            match = _QUEUED.search(output)
            if match is not None and match.group(2) == session:
                return WakeOutcome(
                    ACCEPTED, PROVES, f"queued message {match.group(1)}"[:_MAX_DETAIL]
                )
            return WakeOutcome(
                UNCERTAIN, "", "exit status 0 without the queued line for the session"
            )
        if any(text in errors for text in self._refusals):
            return WakeOutcome(REFUSED, "", f"the daemon refused (exit status {status})")
        return WakeOutcome(UNCERTAIN, "", f"exit status {status} with an unknown error text")


class _NoCustody(OSError):
    """The command was created but could not be owned; it was ended before it ran."""


def _popen(argv: list[str], *, creationflags: int = 0) -> subprocess.Popen[str]:
    """Create the command with no input and both outputs captured."""
    return subprocess.Popen(  # noqa: S603  # nosec B603 - fixed path, argument vector
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        creationflags=creationflags,
    )


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _CREATE_SUSPENDED = 0x00000004
    _ASSIGN_RIGHTS = 0x0100 | 0x0001
    """PROCESS_SET_QUOTA and PROCESS_TERMINATE: what assigning to a job needs."""
    _TH32CS_SNAPTHREAD = 0x00000004
    _THREAD_SUSPEND_RESUME = 0x0002
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _RESUME_FAILED = 0xFFFFFFFF

    class _ThreadEntry(ctypes.Structure):
        """THREADENTRY32 of the tool-help thread snapshot."""

        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        )

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD

    def _job_holding(pid: int) -> object:
        """Return a new job object that holds the process, or None."""
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        process = _kernel32.OpenProcess(_ASSIGN_RIGHTS, False, pid)
        assigned = bool(process) and bool(_kernel32.AssignProcessToJobObject(job, process))
        if process:
            _kernel32.CloseHandle(process)
        if not assigned:
            _kernel32.CloseHandle(job)
            return None
        return job

    def _resume(pid: int) -> int:
        """Resume the threads of a process created suspended; return how many."""
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if not snapshot or snapshot == _INVALID_HANDLE:
            return 0
        resumed = 0
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(_ThreadEntry)
        more = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = _kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if thread:
                    if _kernel32.ResumeThread(thread) != _RESUME_FAILED:
                        resumed += 1
                    _kernel32.CloseHandle(thread)
            more = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        _kernel32.CloseHandle(snapshot)
        return resumed

    def _start_owned(argv: list[str]) -> tuple[subprocess.Popen[str], object]:
        """Create the command suspended, put it into a job of its own, then let it run.

        The command executes nothing before the job holds it, so everything it
        starts belongs to the job. When the job cannot be created or assigned,
        or no thread could be resumed, the process is ended before it ran and
        :class:`_NoCustody` is raised.
        """
        child = _popen(argv, creationflags=_CREATE_SUSPENDED)
        job = _job_holding(child.pid)
        if job is not None and _resume(child.pid) > 0:
            return child, job
        if job is not None:
            _kernel32.TerminateJobObject(job, 1)
            _kernel32.CloseHandle(job)
        child.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.communicate(timeout=END_WAIT_SECONDS)
        raise _NoCustody("the process tree could not be owned")

    def _end_owned(owned: object, child: subprocess.Popen[str]) -> None:
        """End every process of the job, also when the command itself has exited."""
        del child
        _kernel32.TerminateJobObject(owned, 1)

    def _disown(owned: object) -> None:
        """Give the job handle back without ending anything."""
        _kernel32.CloseHandle(owned)

else:

    def _start_owned(argv: list[str]) -> tuple[subprocess.Popen[str], object]:
        """Start the command as the leader of a process group of its own.

        The group exists before the command's program is executed, so
        everything it starts belongs to the group unless it leaves on purpose.
        """
        child = _popen(argv)
        return child, child.pid

    def _end_owned(owned: object, child: subprocess.Popen[str]) -> None:
        """Kill the command's process group, also when the command itself has exited."""
        del owned
        # The group may already be gone; that is the wanted state.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)

    def _disown(owned: object) -> None:
        """Nothing is held for a process group."""
        del owned


def _end(owned: object, child: subprocess.Popen[str], wait_seconds: float) -> None:
    """End the child and what it started, and wait for it for a bounded time.

    A descendant that left the group or the job can keep the output pipes
    open; the wait for the output is therefore bounded too, and the pipes are
    closed when it runs out.
    """
    _end_owned(owned, child)
    try:
        child.communicate(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        for stream in filter(None, (child.stdout, child.stderr)):
            stream.close()
