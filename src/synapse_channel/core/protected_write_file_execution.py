# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — physical protected filesystem operations
"""Explicit physical operations beneath retained enrolled descriptors.

This low-level backend does not authorize execution. The isolated driver must
record an inserted step and establish current authority before calling it.
"""

from __future__ import annotations

import os
import stat
from typing import Literal

from synapse_channel.core.protected_write_descriptors import ProtectedPathDescriptors


def overwrite_retained_protected_file(
    target: ProtectedPathDescriptors, content: bytes, *, max_bytes: int
) -> None:
    """Replace exact bytes on a retained writable regular file without publication.

    Parameters
    ----------
    target:
        Live writable descriptor context for the exact declared before-state.
    content:
        Immutable already verified operation bytes.
    max_bytes:
        Positive enrolled byte budget.

    Raises
    ------
    ValueError
        On invalid content/budget, absent/directory target or stale before-state.
    OSError
        On failed or non-progressing writes or truncation.

    Notes
    -----
    Call only after a newly inserted execution-step record, outside authority
    locks and under proven writer isolation. No grant, journal completion,
    fsync, rename, mode change, cleanup or rollback is implicit. Any exception
    after entry may leave partial bytes; retain custody and reconcile.
    Positional writes preserve descriptor offsets. Truncation happens only after
    all new bytes are written, including the explicit empty-content case.
    """
    _validate_content(content, max_bytes)
    if target.object_descriptor is None or target._expected.is_directory:
        raise ValueError("physical write requires an existing regular target")
    target.revalidate()
    descriptor = target.object_descriptor
    _write_content(descriptor, content)
    os.ftruncate(descriptor, len(content))


def _validate_content(content: bytes, max_bytes: int) -> None:
    if type(max_bytes) is not int or not 0 < max_bytes < 2**53:
        raise ValueError("invalid physical write budget")
    if type(content) is not bytes or len(content) > max_bytes:
        raise ValueError("invalid physical write content")


def _write_content(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.pwrite(descriptor, content[offset : offset + 65536], offset)
        if written <= 0:
            raise OSError("protected positional write made no progress")
        offset += written


def create_retained_protected_entry(
    target: ProtectedPathDescriptors,
    *,
    kind: Literal["file", "directory"],
    mode: int,
    content: bytes,
    max_bytes: int,
) -> None:
    """Create one exact absent entry, without parents, chmod or cleanup.

    Parameters
    ----------
    target:
        Live retained absent target and its pinned parent.
    kind:
        Explicit file or directory operation, never inferred from pathname.
    mode:
        Enrolled permission bits; special and group/other-write bits unsupported.
    content:
        Verified immutable file bytes; must be empty for directory creation.
    max_bytes:
        Positive content budget, also required for empty directory content.

    Raises
    ------
    ValueError
        On invalid input, non-absent target, owner mismatch or resulting mode.
    OSError
        On exclusive creation, writing or descriptor inspection failure.

    Notes
    -----
    Requires the isolated driver's inserted step and current authority. Mode is
    checked, never repaired with chmod or process-global umask changes. A masked
    mode or failed write leaves the created entry for independent recovery.
    File/parent fsync are separate declared operations. No completion is minted.
    """
    _validate_content(content, max_bytes)
    if kind not in ("file", "directory") or (kind == "directory" and content):
        raise ValueError("invalid protected entry kind or directory content")
    if type(mode) is not int or not 0 <= mode <= 0o777 or mode & 0o022:
        raise ValueError("unsupported protected creation mode")
    if target.object_descriptor is not None or os.geteuid() != target._owner_uid:
        raise ValueError("creation requires an absent target and enrolled writer owner")
    target.revalidate()
    if kind == "directory":
        os.mkdir(target.leaf_name, mode, dir_fd=target.parent_descriptor)
        descriptor = os.open(
            target.leaf_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=target.parent_descriptor,
        )
    else:
        descriptor = os.open(
            target.leaf_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            mode,
            dir_fd=target.parent_descriptor,
        )
    try:
        actual = os.fstat(descriptor)
        if stat.S_IMODE(actual.st_mode) != mode or actual.st_uid != target._owner_uid:
            raise ValueError("created entry mode or owner differs from declaration")
        if kind == "file":
            _write_content(descriptor, content)
    finally:
        os.close(descriptor)


def rename_retained_protected_file(
    source: ProtectedPathDescriptors, destination: ProtectedPathDescriptors
) -> None:
    """Rename an exact regular file onto an absent or regular destination.

    Parameters
    ----------
    source:
        Retained existing source file with its declared before-state.
    destination:
        Retained absent/file destination on the same device.

    Raises
    ------
    ValueError
        On a non-file source, directory destination or self-alias.
    OSError
        On failed revalidation or rename, including cross-device moves.

    Notes
    -----
    The driver must prove isolation and record the step first. Revalidation is
    not an atomic inode compare-and-swap against concurrent writers. No fsync,
    cleanup or rollback is implicit; source fsync precedes this declared operation.
    """
    if (
        source.object_descriptor is None
        or source._expected.is_directory
        or destination._expected.is_directory
    ):
        raise ValueError("rename requires file source and absent/file destination")
    if source._expected.file_identity == destination._expected.file_identity:
        raise ValueError("rename source and destination alias the same object")
    source.revalidate()
    destination.revalidate()
    os.rename(
        source.leaf_name,
        destination.leaf_name,
        src_dir_fd=source.parent_descriptor,
        dst_dir_fd=destination.parent_descriptor,
    )


def unlink_retained_protected_file(target: ProtectedPathDescriptors) -> None:
    """Unlink one declared regular file, never a directory or recursive cleanup.

    Parameters
    ----------
    target:
        Retained existing regular target.

    Raises
    ------
    ValueError
        On an absent/directory target or stale before-state.
    OSError
        On failed unlink.

    Notes
    -----
    Requires prior inserted step and writer isolation. Parent fsync remains an
    independent declared operation; this helper never deletes other entries.
    """
    if target.object_descriptor is None or target._expected.is_directory:
        raise ValueError("unlink requires an existing regular file")
    target.revalidate()
    os.unlink(target.leaf_name, dir_fd=target.parent_descriptor)


def fsync_retained_protected_object(target: ProtectedPathDescriptors) -> None:
    """Synchronize one declared existing file or directory descriptor.

    Parameters
    ----------
    target:
        Retained target matching the current declared state.

    Raises
    ------
    ValueError
        On an absent target or stale state.
    OSError
        On failed fsync.

    Notes
    -----
    Call outside authority locks after the inserted execution step. A file fsync
    does not discharge its parent's durability obligation or mint a receipt.
    """
    if target.object_descriptor is None:
        raise ValueError("fsync requires an existing object")
    target.revalidate()
    os.fsync(target.object_descriptor)


class ProtectedDescriptorLocks:
    """Bounded kernel locks retained independently of short-lived path contexts.

    This is resource management, not custody or execution authority. Acquire and
    release require declared inserted steps. close is terminal resource release,
    never a successful substitute for declared reverse-order unlock operations.
    """

    def __init__(self, max_locks: int) -> None:
        """Require a positive explicit retained-descriptor budget."""
        if type(max_locks) is not int or not 0 < max_locks < 2**53:
            raise ValueError("invalid protected lock budget")
        self._max_locks = max_locks
        self._stack: list[tuple[tuple[int, int], int]] = []
        self._closed = False

    def acquire(self, target: ProtectedPathDescriptors) -> None:
        """Acquire one nonblocking exclusive kernel lock in declared order.

        Parameters
        ----------
        target:
            Live retained existing file/directory.

        Raises
        ------
        ValueError
            On closed manager, absent/duplicate object or exhausted budget.
        OSError
            On revalidation, duplication or nonblocking kernel lock failure.
        """
        import fcntl

        if self._closed or target.object_descriptor is None:
            raise ValueError("lock requires an open manager and existing object")
        target.revalidate()
        info = os.fstat(target.object_descriptor)
        identity = (info.st_dev, info.st_ino)
        if len(self._stack) >= self._max_locks or any(item[0] == identity for item in self._stack):
            raise ValueError("duplicate protected lock or exhausted budget")
        descriptor = os.dup(target.object_descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise
        self._stack.append((identity, descriptor))

    def release(self, target: ProtectedPathDescriptors) -> None:
        """Unlock the last acquired physical object, accepting a refreshed path context.

        Parameters
        ----------
        target:
            Current retained observation of the exact last locked inode.

        Raises
        ------
        ValueError
            On closed/empty manager, absent object or wrong release order.
        OSError
            On revalidation or kernel unlock failure.
        """
        import fcntl

        if self._closed or not self._stack or target.object_descriptor is None:
            raise ValueError("unlock requires an existing last locked object")
        target.revalidate()
        if self._stack[-1][0] != target._expected.file_identity:
            raise ValueError("protected unlock violates reverse acquisition order")
        descriptor = self._stack[-1][1]
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        self._stack.pop()

    def close(self) -> None:
        """Release owned descriptors and permanently disable this manager.

        Notes
        -----
        Terminal resource release preserves all filesystem content. Remaining
        locks are not evidence of completed declared unlock steps; the driver
        must stop and reconcile an interrupted operation, never continue it.
        """
        self._closed = True
        while self._stack:
            _, descriptor = self._stack.pop()
            os.close(descriptor)
