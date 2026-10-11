# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owner-only directories whose creation is synced to disk
"""Create an owner-only directory and sync what was created.

A file is not durable until the directory that names it is synced, and a new
directory is not durable until its parent is. :func:`private_dir` creates a
directory with :func:`~synapse_channel.core.private_dir.ensure_private_dir`
and then syncs the parent of every directory it had to make.
:func:`sync_directory` syncs one directory after a create, a rename or a
removal in it.

The syncs are requested on POSIX; elsewhere a directory cannot be opened for
syncing and nothing is done. No power-loss test stands behind this module: it
states which syncs are requested, not a measured guarantee.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from synapse_channel.core.private_dir import PrivateDirError, ensure_private_dir

__all__ = ["private_dir", "sync_directory"]


def _sync_directory_posix(directory: Path) -> None:
    """Make a finished create, rename or removal in ``directory`` durable."""
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# A directory cannot be opened for syncing outside POSIX.
sync_directory: Callable[[Path], None] = (
    _sync_directory_posix if os.name == "posix" else (lambda directory: None)
)


def private_dir(path: Path, *, purpose: str) -> None:
    """Create ``path`` owner-only and sync the parent of each directory made.

    Parameters
    ----------
    path : pathlib.Path
        The directory wanted.
    purpose : str
        Name of the directory in an error.

    Raises
    ------
    PrivateDirError
        The file system root of ``path`` does not exist (a missing volume), or
        the directory cannot be created or proven owner-only.
    """
    missing = []
    current = path
    while not current.is_dir():
        if current == current.parent:
            # A path that is its own parent and is no directory: the root is gone.
            raise PrivateDirError(f"{purpose}: filesystem root is unavailable")
        missing.append(current)
        current = current.parent
    ensure_private_dir(path, parents=True, purpose=purpose)
    for created in missing:
        sync_directory(created.parent)
