# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — record of what a launcher started: executable digest and process identity
"""Record what was started when a session was launched.

A record says three things a later reader cannot learn from the session
itself: which file the command named and what its bytes were, which process
was created, and what image that process was running. It is written once, as
a new owner-only file, at the moment of the launch.

**What a record is worth.** It is written by a launcher that runs under the
same account as the session it starts. Whoever controls that account can
write the same file. So a record is the seat's own declaration, kept at the
time, and it says so in its ``evidence_class``. It is not an attestation: it
does not show which provider the process connected to, and a reader that
needs an authority outside the account needs an observer outside it.

**What is hashed.** The file named by the command's first word, resolved
through the search path and through links, hashed from one open descriptor.
When that file is a script, the bytes hashed are the script's; the interpreter
is then visible as the image of the process. On Linux the image is read
through the kernel's own link to the running program, so a file replaced after
the start does not change what is recorded for the process. On Windows the
image is read by the path the system reports for the process.

**What is only stated.** A caller may give the client's vendor and a
reference to the seat's public key. Both are stored exactly as stated: nothing
here checks the vendor against the program, or that the seat holds the key.

**What is not stored.** The command's further words can hold secrets. Only
their number and a digest of the whole command are kept.

A process is identified by its number together with when it started, because
a number alone is reused: on Linux the boot identifier and the start time in
clock ticks, on Windows the creation time. On other systems the process part
of a record is stated as unavailable, with the reason.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.private_dir import ensure_private_dir

SCHEMA = "session-launch-record-v1"
"""Schema name of a launch record as plain data."""

EVIDENCE_CLASS = "declared_by_launcher_under_the_seat_account"
"""What every record written here is: the seat's own declaration, not an attestation."""

MAX_HASHED_BYTES = 1024 * 1024 * 1024
"""Largest file hashed; a larger one is recorded without a digest."""

_CHUNK = 1024 * 1024
_MAX_TEXT = 256
_UNSAFE = "".join(chr(code) for code in range(32)) + "\x7f"


class LaunchRecordError(SynapseError, ValueError):
    """A launch record cannot be made from what was given."""

    code = "launch_record"


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """A file as found: its resolved path, its size and the digest of its bytes.

    ``sha256`` is empty when the file could not be hashed; ``unavailable``
    then says why. ``size`` is -1 when the total size could not be established.
    """

    path: str
    size: int = -1
    sha256: str = ""
    unavailable: str = ""


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """A process as found: its number, when it started, and the image it runs.

    ``boot`` and ``started`` are empty when the system gives no start
    identity; ``unavailable`` then says why.
    """

    pid: int
    boot: str = ""
    started: str = ""
    image: FileIdentity | None = None
    unavailable: str = ""


@dataclass(frozen=True, slots=True)
class LaunchRecord:
    """One launch: who asked, what was named, what was started."""

    seat: str
    recorded_at: str
    host: str
    system: str
    command_name: str
    command_words: int
    command_sha256: str
    executable: FileIdentity
    process: ProcessIdentity
    launcher_pid: int
    declared_vendor: str = ""
    seat_public_key: str = ""
    evidence_class: str = EVIDENCE_CLASS

    def to_json(self) -> dict[str, object]:
        """Return the record as plain data."""
        return {"schema": SCHEMA, **asdict(self)}


def file_identity(
    path: str | os.PathLike[str], *, limit: int = MAX_HASHED_BYTES, shown: str | None = None
) -> FileIdentity:
    """Return the size and the digest of a file, read from one open descriptor.

    Parameters
    ----------
    path : path
        The file to open. Links are followed by the open itself.
    limit : int
        Largest file hashed.
    shown : str or None
        The path to record; the resolved ``path`` by default.

    Returns
    -------
    FileIdentity
        The path, size and digest. A file that cannot be opened, is not a
        regular file or is larger than ``limit`` is returned without a digest
        and with the reason. Reads use at most the remaining allowance plus one
        byte to detect overflow; an unread total size is recorded as -1.
    """
    named = os.path.realpath(path) if shown is None else shown
    # Without O_NONBLOCK, opening a named pipe would wait for a writer.
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        return FileIdentity(named, unavailable=f"cannot be opened: {type(exc).__name__}")
    status = os.fstat(descriptor)
    if not stat.S_ISREG(status.st_mode) or status.st_size > limit:
        os.close(descriptor)
        if not stat.S_ISREG(status.st_mode):
            return FileIdentity(named, unavailable="not a regular file")
        return FileIdentity(named, status.st_size, unavailable="larger than the limit")
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(descriptor, "rb") as handle:
        while chunk := handle.read(min(_CHUNK, limit - size + 1)):
            size += len(chunk)
            if size > limit:
                return FileIdentity(named, unavailable="larger than the limit")
            digest.update(chunk)
    return FileIdentity(named, size, digest.hexdigest())


def executable_identity(
    command: Sequence[str], environ: Mapping[str, str] | None = None
) -> FileIdentity:
    """Return the identity of the file the command's first word names.

    The word is resolved through the search path of ``environ`` (the process
    environment by default). On POSIX, an environment without ``PATH`` uses
    the default executable search path, as a direct process start does.

    Raises
    ------
    LaunchRecordError
        The command is empty.
    """
    if not command or not command[0]:
        raise LaunchRecordError("a launch record needs a command")
    env = os.environ if environ is None else environ
    path = env.get("PATH")
    if path is None and os.name == "posix":
        path = os.defpath
    found = shutil.which(command[0], path=path)
    if found is None:
        return FileIdentity(command[0], unavailable="not found on the search path")
    return file_identity(found)


def process_identity(pid: int) -> ProcessIdentity:
    """Return the start identity of a running process and the identity of its image.

    Nothing is raised for a process that has gone or cannot be read: the
    record then says what is unavailable, and why.

    Raises
    ------
    LaunchRecordError
        ``pid`` is not a positive whole number.
    """
    if type(pid) is not int or pid <= 0:
        raise LaunchRecordError("a process number is a positive whole number")
    if sys.platform.startswith("linux"):
        return _linux_process(pid)
    if sys.platform == "win32":
        return _windows_process(pid)
    return ProcessIdentity(pid, unavailable=f"no start identity is read on {sys.platform}")


def _linux_process(pid: int) -> ProcessIdentity:
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        # The command name sits in brackets and may hold spaces and brackets itself.
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    except (OSError, IndexError, UnicodeDecodeError) as exc:
        return ProcessIdentity(pid, unavailable=f"cannot be read: {type(exc).__name__}")
    if fields[0] in {"Z", "X"}:
        return ProcessIdentity(pid, unavailable="the process has exited")
    link = f"/proc/{pid}/exe"
    try:
        shown = os.readlink(link)
    except OSError as exc:
        image = FileIdentity("", unavailable=f"cannot be named: {type(exc).__name__}")
    else:
        # Opening the link opens the running program itself, also when the
        # file at that path was replaced or removed after the start.
        image = file_identity(link, shown=shown)
    return ProcessIdentity(pid, boot, fields[19], image)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _QUERY_LIMITED = 0x1000
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _STILL_ACTIVE = 259

    def _windows_process(pid: int) -> ProcessIdentity:
        handle = _kernel32.OpenProcess(_QUERY_LIMITED, False, pid)
        if not handle:
            return ProcessIdentity(pid, unavailable="the process cannot be opened")
        try:
            # A process that ended stays openable while anyone holds a handle to it,
            # as an uncollected one stays in the Linux table: it is given no identity.
            status = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(status)):
                return ProcessIdentity(pid, unavailable="the process state cannot be read")
            if status.value != _STILL_ACTIVE:
                return ProcessIdentity(pid, unavailable="the process has ended")
            times = [wintypes.FILETIME() for _ in range(4)]
            if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                return ProcessIdentity(pid, unavailable="the start time cannot be read")
            started = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            named = _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length))
            image = (
                file_identity(buffer.value)
                if named
                else FileIdentity("", unavailable="the image cannot be named")
            )
            return ProcessIdentity(pid, "", started, image)
        finally:
            _kernel32.CloseHandle(handle)


def _clean(value: str, what: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > _MAX_TEXT or any(character in _UNSAFE for character in text):
        raise LaunchRecordError(f"{what} is 1 to {_MAX_TEXT} characters without control characters")
    return text


def make_record(
    *,
    seat: str,
    command: Sequence[str],
    pid: int,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
    declared_vendor: str = "",
    seat_public_key: str = "",
) -> LaunchRecord:
    """Return the record of one launch: the command as named and the process as started.

    Parameters
    ----------
    seat : str
        The identity the session was started for.
    command : sequence of str
        The command that was started. Only its first word's base name, the
        number of its words and a digest of all of them enter the record.
    pid : int
        The process the launcher created.
    environ : mapping or None
        The environment the command was started with; its search path
        resolves the first word.
    now : datetime or None
        The moment recorded; the present by default.
    declared_vendor : str
        The vendor the caller states for the client, or an empty text. It is
        stored as stated and checked against nothing.
    seat_public_key : str
        A reference to the seat's public key as the caller states it, or an
        empty text. Possession of the key is not checked.

    Raises
    ------
    LaunchRecordError
        The seat or the command is empty or malformed, or ``pid`` is not a
        positive whole number.
    """
    executable = executable_identity(command, environ)
    if not all(isinstance(word, str) for word in command):
        raise LaunchRecordError("a command is a list of texts")
    whole = json.dumps(list(command), ensure_ascii=True, separators=(",", ":"))
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return LaunchRecord(
        seat=_clean(seat, "the seat"),
        recorded_at=moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        host=socket.gethostname(),
        system=sys.platform,
        command_name=_clean(os.path.basename(command[0]), "the command's name"),
        command_words=len(command),
        command_sha256=hashlib.sha256(whole.encode("ascii")).hexdigest(),
        executable=executable,
        process=process_identity(pid),
        launcher_pid=os.getpid(),
        declared_vendor=_clean(declared_vendor, "the declared vendor") if declared_vendor else "",
        seat_public_key=_clean(seat_public_key, "the key reference") if seat_public_key else "",
    )


def record_dir_for(environ: Mapping[str, str]) -> Path | None:
    """Return where launch records are kept for an environment, or ``None``.

    The directory lies under the state directory that the environment names,
    or under its home directory. An environment that names neither has no
    place for records, and ``None`` is returned rather than a guessed path.
    """
    state = environ.get("XDG_STATE_HOME", "").strip()
    home = environ.get("HOME", "").strip() or environ.get("USERPROFILE", "").strip()
    if state:
        return Path(state) / "synapse" / "launch-records"
    if home:
        return Path(home) / ".local" / "state" / "synapse" / "launch-records"
    return None


def write_record(directory: Path, record: LaunchRecord) -> Path:
    """Write one record as a new owner-only file and return its path.

    The file is created and never replaced: two launches at the same moment
    write two files. The directory is made, or proven, accessible to its owner
    alone before anything is put in it. The file is owner-only from its
    creation: by its permission bits where those are the floor, and on Windows
    by the access list it inherits from that directory, which names the one
    account.

    Raises
    ------
    PrivateDirError
        The directory cannot be created or cannot be proven owner-only: it is
        a link, not a directory, or belongs to another account.
    OSError
        The file cannot be created or written.
    """
    ensure_private_dir(directory, parents=True, purpose="launch record directory")
    seat = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in record.seat)[:80]
    stamp = record.recorded_at.replace("-", "").replace(":", "").replace(".", "")
    path = directory / f"{seat}__{stamp}__{record.process.pid}__{secrets.token_hex(4)}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record.to_json(), handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path
