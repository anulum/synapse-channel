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

from synapse_channel.core.private_dir import ensure_private_dir

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


@contextmanager
def locked(path: Path, *, blocking: bool, purpose: str = "lock directory") -> Iterator[bool]:
    """Hold an exclusive advisory lock on the file ``path``.

    Parameters
    ----------
    path : pathlib.Path
        Lock file; created owner-only when absent, in a directory that is
        created and checked as owner-only.
    blocking : bool
        Wait for the lock, or report at once that someone else holds it.
    purpose : str
        Name of the directory in an error about its ownership or mode.

    Yields
    ------
    bool
        Whether the lock is held. A blocking request always holds it.
    """
    ensure_private_dir(path.parent, parents=True, purpose=purpose)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        held = _take(descriptor, blocking)
        try:
            yield held
        finally:
            if held:
                _release(descriptor)
    finally:
        os.close(descriptor)
