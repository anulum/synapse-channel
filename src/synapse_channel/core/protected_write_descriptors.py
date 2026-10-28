# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained protected path descriptors
"""Retain and revalidate enrolled POSIX paths without mutating their contents."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from synapse_channel.core.protected_write_inspection import (
    ProtectedFileInspection,
    inspect_protected_file,
)
from synapse_channel.core.protected_write_openat2 import verify_protected_openat2_path


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _check_owner(info: os.stat_result, owner_uid: int) -> None:
    if info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise ValueError("protected object violates enrolled owner and mode requirements")


def _check_chain(
    chain: tuple[int, ...],
    parts: tuple[str, ...],
    expected: ProtectedFileInspection,
    owner_uid: int,
) -> None:
    for index, descriptor in enumerate(chain):
        info = os.fstat(descriptor)
        _check_owner(info, owner_uid)
        if not stat.S_ISDIR(info.st_mode) or _identity(info) != expected.directories[index]:
            raise ValueError("retained protected directory identity mismatch")
        if index:
            linked = os.stat(parts[index - 1], dir_fd=chain[index - 1], follow_symlinks=False)
            if _identity(linked) != _identity(info):
                raise ValueError("protected directory attachment changed")


def _check_file_content(
    descriptor: int,
    before: os.stat_result,
    expected: ProtectedFileInspection,
    max_bytes: int,
    *,
    capture: bool = False,
) -> bytes:
    """Verify bounded retained bytes and unchanged metadata using positional reads."""
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("protected object is not a single-link regular file")
    if before.st_size != expected.size_bytes or before.st_size > max_bytes:
        raise ValueError("protected object size changed")
    total = 0
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    while True:
        chunk = os.pread(descriptor, min(65536, max_bytes + 1 - total), total)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("protected object grew beyond retained byte budget")
        digest.update(chunk)
        if capture:
            chunks.append(chunk)
    after = os.fstat(descriptor)
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_gid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if (
        total != expected.size_bytes
        or digest.hexdigest() != expected.sha256
        or any(getattr(before, field) != getattr(after, field) for field in fields)
    ):
        raise ValueError("protected retained bytes changed")
    return b"".join(chunks)


@dataclass(frozen=True)
class ProtectedPathDescriptors:
    """Live descriptors owned by hold_protected_path, valid only in its context.

    No descriptor is an authority token. The isolated writer must separately hold
    current begin, custody and an inserted execution-step record. This object
    neither performs mutations nor proves exclusion of concurrent writers.
    """

    parent_descriptor: int
    object_descriptor: int | None
    leaf_name: str
    _chain: tuple[int, ...]
    _parts: tuple[str, ...]
    _expected: ProtectedFileInspection
    _owner_uid: int
    _max_bytes: int
    _require_openat2: bool = field(default=False, kw_only=True)
    _closed: bool = False

    def revalidate(self) -> None:
        """Verify current attachments, enrolled ownership and exact retained bytes.

        Raises
        ------
        ValueError
            On stale/replaced topology, object identity, mode, bytes or ownership.
        OSError
            On inaccessible or detached components.

        Notes
        -----
        Uses descriptor-relative nofollow stat and positional reads, preserving
        offsets. This narrows stale-observation windows but cannot atomically
        exclude a concurrent rename/write. Enforced OS-principal isolation is
        mandatory before using these descriptors to mutate files.
        """
        self._revalidate(capture=False)

    def read_bytes(self) -> bytes:
        """Capture the exact inspected file bytes without resolving an external path.

        Returns
        -------
        bytes
            Immutable content matching the enrolled size and raw-byte SHA-256.
            Positional reads preserve the retained file's current offset.

        Raises
        ------
        ValueError
            On a closed context, absent/directory target, changed identity,
            content, attachments, ownership or exhausted byte budget.
        OSError
            On inaccessible retained descriptors or filesystem read failure.

        Notes
        -----
        The capture and a final revalidation both use the retained root and
        object descriptors. Replacing the external root pathname cannot redirect
        this read. Multi-file coherence, authentication, current authority and
        enforced exclusion of concurrent writers remain service prerequisites.
        This method creates no read permission or active-memory receipt.
        """
        if self.object_descriptor is None or self._expected.is_directory:
            raise ValueError("protected byte capture requires an existing regular file")
        content = self._revalidate(capture=True)
        self.revalidate()
        return content

    def _revalidate(self, *, capture: bool) -> bytes:
        """Check retained topology and optionally return the verified content read."""
        if self._closed:
            raise ValueError("protected descriptor context is closed")
        _check_chain(self._chain, self._parts, self._expected, self._owner_uid)
        if self._require_openat2:
            verify_protected_openat2_path(
                self._chain[0],
                self._expected.relative_path,
                self.parent_descriptor,
                self.object_descriptor,
            )
        try:
            linked = os.stat(self.leaf_name, dir_fd=self.parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            if self.object_descriptor is not None:
                raise ValueError("protected object attachment disappeared") from None
            return b""
        if self.object_descriptor is None:
            raise ValueError("protected absent leaf appeared")
        before = os.fstat(self.object_descriptor)
        _check_owner(before, self._owner_uid)
        if (
            _identity(linked) != _identity(before)
            or _identity(before) != self._expected.file_identity
        ):
            raise ValueError("protected object attachment changed")
        if stat.S_IMODE(before.st_mode) != self._expected.mode:
            raise ValueError("protected object mode changed")
        if self._expected.is_directory:
            return b""
        return _check_file_content(
            self.object_descriptor, before, self._expected, self._max_bytes, capture=capture
        )


@contextmanager
def hold_protected_path(
    root_descriptor: int,
    expected: ProtectedFileInspection,
    *,
    owner_uid: int,
    max_bytes: int,
    writable: bool = False,
    require_openat2: bool = False,
) -> Iterator[ProtectedPathDescriptors]:
    """Retain an exact previously inspected path without truncation or creation.

    Parameters
    ----------
    root_descriptor:
        Borrowed enrolled directory descriptor; never closed here.
    expected:
        Trusted observation from the enrolled root, not a client-selected path.
    owner_uid:
        Explicit enrolled filesystem owner for every ancestor and target.
    max_bytes:
        Positive bounded byte budget for repeated exact content observations.
    writable:
        Open an existing regular target read/write without truncation. Directories
        and absent targets must use False. This does not authorize a later write.
    require_openat2:
        Verify each revalidation through Linux openat2 with beneath, no-symlink
        and no-mount-crossing resolution. Unsupported native profiles refuse.

    Yields
    ------
    ProtectedPathDescriptors
        Retained root/ancestor/target descriptors, closed on every context exit.

    Raises
    ------
    ValueError
        On changed observation, unsafe ownership, invalid option or identity.
    OSError
        If descriptor traversal fails.

    Notes
    -----
    No create, truncate, chmod, unlink, implicit cleanup or fsync is performed.
    Caller offsets are not changed. No symlink or same-content replacement is
    accepted. Group/other-writable ancestors and objects fail closed. Descriptor
    closure is resource release, not recovery of a partially executed operation.
    The root remains pinned by its borrowed descriptor, not its external pathname.
    """
    if (
        type(owner_uid) is not int
        or owner_uid < 0
        or type(writable) is not bool
        or type(require_openat2) is not bool
    ):
        raise ValueError("invalid enrolled descriptor owner or access option")
    if not expected.directories:
        raise ValueError("retained path has no enrolled root")
    if writable and (expected.file_identity is None or expected.is_directory):
        raise ValueError("writable descriptor requires an existing regular file")
    observed = inspect_protected_file(
        root_descriptor,
        expected.relative_path,
        root_identity=expected.directories[0],
        max_bytes=max_bytes,
        allow_directory=expected.is_directory,
        require_openat2=require_openat2,
    )
    if observed != expected:
        raise ValueError("protected observation changed before retention")
    descriptors: list[int] = []
    held = None
    try:
        descriptors.append(os.dup(root_descriptor))
        parts = tuple(expected.relative_path.split("/"))
        flags = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        for part in parts[:-1]:
            descriptors.append(
                os.open(part, flags | os.O_RDONLY | os.O_DIRECTORY, dir_fd=descriptors[-1])
            )
        parent = descriptors[-1]
        _check_chain(tuple(descriptors), parts, expected, owner_uid)
        leaf = None
        if expected.file_identity is not None:
            leaf = os.open(
                parts[-1], flags | (os.O_RDWR if writable else os.O_RDONLY), dir_fd=parent
            )
            descriptors.append(leaf)
        held = ProtectedPathDescriptors(
            parent,
            leaf,
            parts[-1],
            tuple(descriptors if leaf is None else descriptors[:-1]),
            parts,
            expected,
            owner_uid,
            max_bytes,
            _require_openat2=require_openat2,
        )
        held.revalidate()
        yield held
    finally:
        if held is not None:
            object.__setattr__(held, "_closed", True)
        for descriptor in reversed(descriptors):
            os.close(descriptor)
