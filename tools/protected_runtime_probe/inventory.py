# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded descriptor-relative runtime inventory
"""Compare actual directories with only the tree implied by an approved file set."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager

from .capsule import MAX_FILES

_MAX_DEPTH = 64
_MAX_ENTRIES = 20_000
_MAX_NAME_BYTES = 8 * 1024 * 1024


def _parts(name: str) -> tuple[str, ...]:
    """Require a bounded canonical name before deriving directory expectations."""
    if (
        type(name) is not str
        or not 0 < len(name.encode("utf-8")) <= 4096
        or chr(92) in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise ValueError("invalid runtime inventory path")
    parts = tuple(name.split("/"))
    if len(parts) > _MAX_DEPTH or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid runtime inventory ancestry")
    return parts


def _expected_tree(files: frozenset[str]) -> dict[str, dict[str, bool]]:
    """Bound every directory and name before opening any untrusted descendant."""
    if type(files) is not frozenset or not 0 < len(files) <= MAX_FILES:
        raise ValueError("invalid runtime inventory count")
    tree: dict[str, dict[str, bool]] = {"": {}}
    entry_count = 0
    name_bytes = 0
    for name in files:
        parts = _parts(name)
        parent = ""
        for index, part in enumerate(parts):
            directory = index < len(parts) - 1
            entries = tree[parent]
            relative = parent + "/" + part if parent else part
            if part in entries:
                if entries[part] != directory:
                    raise ValueError("runtime inventory file overlaps a directory")
            else:
                entries[part] = directory
                entry_count += 1
                name_bytes += len(relative.encode("utf-8"))
                if entry_count > _MAX_ENTRIES or name_bytes > _MAX_NAME_BYTES:
                    raise ValueError("runtime inventory metadata exceeds its budget")
            if directory:
                parent = relative
                tree.setdefault(parent, {})
    return tree


@contextmanager
def _directory(root: int, relative: str) -> Iterator[int]:
    """Borrow one exact descendant using a constant number of no-follow descriptors."""
    current = os.dup(root)
    try:
        for component in relative.split("/") if relative else ():
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            os.close(current)
            current = child
        yield current
    finally:
        os.close(current)


def verify_directory_inventory(root: int, expected_files: frozenset[str]) -> None:
    """Require the exact bounded tree through a retained native POSIX directory.

    Parameters
    ----------
    root:
        Borrowed directory descriptor, retained by the caller through byte capture.
    expected_files:
        Independently pinned relative regular-file names. Only their parent
        directories are allowed; extra empty trees, links and special files fail.

    Raises
    ------
    ValueError
        On invalid enrollment, excess metadata, missing/extra names, linked files
        or a different directory/file topology. Expected depth is at most 64,
        entries at most 20,000 and retained relative names at most 8 MiB.
    OSError
        On inaccessible ancestry or unavailable native descriptor operations.

    Notes
    -----
    Each scan stops at its first unlisted or duplicate name. Descendant paths
    are opened relative to the retained root without following symlinks. Only
    a constant number of descriptors is owned at once, even for a broad tree.
    This checks topology, not immutable content or root ownership. The caller
    must still capture stable regular bytes and verify every independent pin;
    concurrently replaced files cannot gain trust from this observation.
    """
    if type(root) is not int or root < 0:
        raise ValueError("invalid runtime inventory descriptor")
    tree = _expected_tree(expected_files)
    if not stat.S_ISDIR(os.fstat(root).st_mode):
        raise ValueError("runtime inventory root is not a directory")
    for relative, expected in tree.items():
        with _directory(root, relative) as descriptor, os.scandir(descriptor) as entries:
            observed: set[str] = set()
            for entry in entries:
                if entry.name not in expected or entry.name in observed:
                    raise ValueError("runtime inventory contains an unlisted name")
                info = entry.stat(follow_symlinks=False)
                if expected[entry.name]:
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError("runtime inventory directory was replaced")
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("runtime inventory file is linked or nonregular")
                observed.add(entry.name)
            if observed != expected.keys():
                raise ValueError("runtime inventory is missing an enrolled name")
