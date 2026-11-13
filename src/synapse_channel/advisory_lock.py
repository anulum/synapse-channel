# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one advisory file lock for POSIX and Windows
"""Hold an advisory lock on a file, the same way on POSIX and on Windows.

``flock`` on POSIX, a byte-range lock through ``msvcrt`` on Windows. The lock
belongs to the open file: it ends with the block and with the process if it
dies, so no stale lock is left behind.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from synapse_channel.durable_dir import private_dir

__all__ = ["locked"]

if sys.platform == "win32":
    import errno
    import msvcrt

    _HELD_BY_ANOTHER = (errno.EACCES, errno.EDEADLK)
    """What the C runtime reports for a region locked by someone else."""

    def _take(descriptor: int, blocking: bool) -> bool:
        """Lock the first byte; a blocking request asks again until it has it.

        Only a region held by someone else is waited for or reported as busy.
        Any other error of the descriptor or the file system is raised.
        """
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                return True
            except OSError as exc:
                if exc.errno not in _HELD_BY_ANOTHER:
                    raise
                if not blocking:
                    return False
                time.sleep(0.02)

    def _release(descriptor: int) -> None:
        """Unlock the first byte."""
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

    def _delete_pending(exc: OSError, path: Path) -> bool:
        """Say whether ``exc`` is a delete-pending name, not a hard failure.

        The hosted Windows job runs this. Unlink while a handle is open
        leaves the name pending until that handle closes, and ``CreateFileW``
        then returns ``ERROR_ACCESS_DENIED`` (5). A directory at the same
        name also denies that open, and it can still be stated, so it stays
        a hard failure. A pending name cannot be stated. The caller retries
        a pending name and then reports the lock as not acquired.
        """
        if getattr(exc, "winerror", None) != 5:
            return False
        try:
            os.lstat(path)
        except OSError:
            return True
        return False

    def _open_windows_delete_share(path: Path) -> int:
        """Open or create ``path`` with delete sharing and no earlier handle.

        The hosted Windows job runs this opener. A C runtime open does not
        share delete, so it is a sharing violation while any handle holds
        ``DELETE`` access. This is the only open for a removable lock:
        ``OPEN_ALWAYS`` creates a missing file with the same owner mode the
        C runtime open uses (``0o600``), and a second opener can open the
        name and then meet the byte-range lock. A delete-pending name raises
        ``ERROR_ACCESS_DENIED``. The caller closes the descriptor.
        """
        import ctypes
        import msvcrt
        from ctypes import wintypes
        from typing import Any

        kernel: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(
            str(path),
            0xC0010000,  # GENERIC_READ | GENERIC_WRITE | DELETE
            0x00000007,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
            None,
            4,  # OPEN_ALWAYS
            0x80,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        error = ctypes.get_last_error()
        invalid = ctypes.c_void_p(-1).value
        if not handle or handle == invalid:
            raise ctypes.WinError(error)
        if error != 183:  # ERROR_ALREADY_EXISTS: the name was already there
            try:
                os.chmod(path, 0o600)
            except OSError:
                kernel.CloseHandle(handle)
                raise
        descriptor_flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        try:
            return int(msvcrt.open_osfhandle(int(handle), descriptor_flags))
        except OSError:
            kernel.CloseHandle(handle)
            raise

    def _open_lock(path: Path, *, removable: bool) -> int:
        """Open the lock file.

        A removable lock uses only the delete-sharing open, including when
        the file is absent. The hosted Windows job runs that path. Any other
        lock uses the C runtime open, which has no delete sharing.
        """
        if removable:
            return _open_windows_delete_share(path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        return os.open(path, flags, 0o600)

else:
    import fcntl

    def _take(descriptor: int, blocking: bool) -> bool:
        """Take an exclusive ``flock``; a blocking request waits for it."""
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            return False
        return True

    def _release(descriptor: int) -> None:
        """Give the ``flock`` back."""
        fcntl.flock(descriptor, fcntl.LOCK_UN)

    def _open_lock(path: Path, *, removable: bool) -> int:
        """Open the lock file owner-only.

        ``removable`` changes the Windows opener only. POSIX can unlink an
        open file, so the flag does not change this descriptor.
        """
        del removable
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        return os.open(path, flags, 0o600)

    def _delete_pending(exc: OSError, path: Path) -> bool:
        """POSIX has no delete-pending name; an open error stays an open error."""
        del exc, path
        return False


def _same_inode(descriptor: int, path: Path) -> bool:
    """Return whether ``path`` still names the open file.

    The comparison is ``st_dev`` and ``st_ino``. A missing path is not the
    same file. On Windows ``st_ino`` is the file index, and the hosted
    Windows job runs this comparison for a removable lock.
    """
    held = os.fstat(descriptor)
    try:
        named = os.lstat(path)
    except OSError:
        return False
    return (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino)


_IDENTITY_ATTEMPTS = 4
"""How many times a removable lock may open again after the name moves."""


def _acquire(path: Path, *, blocking: bool, removable: bool) -> int | None:
    """Return a descriptor holding the lock, or None when it was not acquired.

    Callers that do not remove the file try once and do not compare inodes.
    A removable lock releases an inode the path no longer names and opens
    again, a few times, then reports the lock as not acquired. On Windows a
    delete-pending name refuses the open with access denied until its last
    handle closes; that refusal is the same moved name, not an unavailable
    lock.
    """
    attempts = _IDENTITY_ATTEMPTS if removable else 1
    for _attempt in range(attempts):
        try:
            descriptor = _open_lock(path, removable=removable)
        except OSError as exc:
            if removable and _delete_pending(exc, path):
                continue
            raise
        keep = False
        try:
            if not _take(descriptor, blocking):
                return None
            if not removable or _same_inode(descriptor, path):
                keep = True
            else:
                _release(descriptor)
        finally:
            if not keep:
                os.close(descriptor)
        if keep:
            return descriptor
    return None


@contextmanager
def locked(
    path: Path,
    *,
    blocking: bool,
    purpose: str = "lock directory",
    removable: bool = False,
) -> Iterator[bool]:
    """Hold an exclusive advisory lock on the file ``path``.

    Parameters
    ----------
    path : pathlib.Path
        Lock file; created owner-only when absent, in a directory that is
        created and checked as owner-only, with the parent of every directory
        made for it synced.
    blocking : bool
        Wait for the lock, or report at once that someone else holds it.
    purpose : str
        Name of the directory in an error about its ownership or mode.
    removable : bool, optional
        When true, a holder may unlink the file before it releases the lock.
        After the lock is taken, the path must still name that file
        (``st_dev`` and ``st_ino``). On Windows ``st_ino`` is the file index;
        the hosted Windows job runs that comparison. A missing path or a
        different inode is released and the open is repeated a few times.
        If every attempt misses, the lock is not acquired. On Windows the
        only open is ``CreateFileW`` with delete sharing, so a second opener
        reaches the byte-range lock. A delete-pending name
        (``ERROR_ACCESS_DENIED`` while a handle remains) is a moved name:
        it is retried, then not acquired. The default is false: one open and
        no inode comparison, which is every caller that does not remove the
        lock file.

    Yields
    ------
    bool
        Whether the lock is held. A blocking request holds it unless a
        removable lock exhausts its identity attempts.
    """
    private_dir(path.parent, purpose=purpose)
    descriptor = _acquire(path, blocking=blocking, removable=removable)
    if descriptor is None:
        yield False
        return
    try:
        yield True
    finally:
        _release(descriptor)
        os.close(descriptor)
