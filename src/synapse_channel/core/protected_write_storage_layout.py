# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected journal physical layout
"""Read-only physical layout checks, not SQLite connection identity attestation."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectedJournalLayout:
    """Point-in-time private-root and exact database/sidecar identities and sizes."""

    root_identity: tuple[int, int]
    files: tuple[tuple[str, int, int, int], ...]


def inspect_protected_journal_layout(
    root_descriptor: int,
    database_name: str,
    *,
    root_identity: tuple[int, int],
    owner_uid: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> ProtectedJournalLayout:
    """Check an existing private WAL-layout directory without chmod or hashing.

    Parameters
    ----------
    root_descriptor:
        Borrowed enrolled directory descriptor, never closed here.
    database_name:
        Exact simple database basename; no path or URI resolution is accepted.
    root_identity:
        Operator-pinned directory device/inode tuple.
    owner_uid:
        Enrolled filesystem owner.
    max_file_bytes:
        Positive per-file size cap covering database and each optional sidecar.
    max_total_bytes:
        Positive aggregate cap for database, WAL and SHM bytes.

    Returns
    -------
    ProtectedJournalLayout
        Observed exact names, device/inode identities and sizes. This is not an
        authority token or proof that an existing SQLite connection owns them.

    Raises
    ------
    ValueError
        On invalid enrollment, ownership/mode, unsafe type/link/device, changing
        object, rollback-journal residue or exceeded budget.
    OSError
        On missing database or inaccessible descriptor.

    Notes
    -----
    Run during service quiescence. Root requires 0700 and files 0600, exact owner
    and single hard link. Only database, -wal and -shm are inspected; unrelated
    names confer no authority. An existing -journal is rejected for this WAL
    profile. No content reads, file creation, cleanup or mode repair occur.
    Enforced principal isolation and connection-to-file binding remain separate
    mandatory gates; rechecking a pathname cannot prove SQLite handle identity.
    """
    if type(owner_uid) is not int or owner_uid < 0:
        raise ValueError("invalid storage owner")
    if (
        type(root_identity) is not tuple
        or len(root_identity) != 2
        or any(type(value) is not int or value < 0 for value in root_identity)
    ):
        raise ValueError("invalid storage root identity")
    if any(
        type(value) is not int or not 0 < value < 2**53
        for value in (max_file_bytes, max_total_bytes)
    ):
        raise ValueError("invalid storage size budget")
    if (
        not isinstance(database_name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", database_name) is None
    ):
        raise ValueError("invalid storage database basename")
    descriptor = os.dup(root_descriptor)
    try:
        root = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(root.st_mode)
            or (root.st_dev, root.st_ino) != root_identity
            or root.st_uid != owner_uid
            or stat.S_IMODE(root.st_mode) != 0o700
        ):
            raise ValueError("storage root identity or private ownership mismatch")
        try:
            os.stat(database_name + "-journal", dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("unexpected rollback journal in protected WAL layout")
        files: list[tuple[str, int, int, int]] = []
        total = 0
        for suffix in ("", "-wal", "-shm"):
            name = database_name + suffix
            try:
                before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                if not suffix:
                    raise
                continue
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_dev != root.st_dev
                or before.st_uid != owner_uid
                or stat.S_IMODE(before.st_mode) != 0o600
            ):
                raise ValueError("unsafe protected storage file")
            leaf = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=descriptor
            )
            try:
                opened = os.fstat(leaf)
                linked = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                fields = (
                    "st_dev",
                    "st_ino",
                    "st_uid",
                    "st_gid",
                    "st_mode",
                    "st_nlink",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
                if any(
                    getattr(before, field) != getattr(item, field)
                    for item in (opened, linked)
                    for field in fields
                ):
                    raise ValueError("protected storage changed during inspection")
                total += opened.st_size
                if opened.st_size > max_file_bytes or total > max_total_bytes:
                    raise ValueError("protected storage exceeds size budget")
                files.append((name, opened.st_dev, opened.st_ino, opened.st_size))
            finally:
                os.close(leaf)
        return ProtectedJournalLayout(root_identity, tuple(files))
    finally:
        os.close(descriptor)
