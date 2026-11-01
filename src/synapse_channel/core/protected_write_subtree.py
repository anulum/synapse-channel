# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained writer process subtree observations
"""Read recursive Linux cgroup population without certifying recovery authority."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass

from synapse_channel.core.protected_write_cgroup_controls import (
    ProtectedCgroupControlLevel,
    capture_protected_cgroup_controls,
)
from synapse_channel.core.protected_write_cgroup_parsing import (
    parse_protected_cgroup_events,
    parse_protected_cgroup_membership,
)


class ProtectedWriterSubtreeStateChanged(ValueError):
    """Two valid kernel event snapshots changed while retained metadata stayed stable."""


@dataclass(frozen=True)
class ProtectedWriterSubtreeObservation:
    """Kernel population of a retained subtree, not physical custody or a permit."""

    device: int
    inode: int
    owner_uid: int
    populated: bool
    frozen: bool
    controls: tuple[ProtectedCgroupControlLevel, ...]


def inspect_protected_writer_subtree(directory_fd: int) -> ProtectedWriterSubtreeObservation:
    """Read bounded recursive population from an already retained cgroup descriptor.

    Parameters
    ----------
    directory_fd:
        Supervisor-held cgroup v2 directory descriptor. Acquisition, root-owned
        control and writer membership must be enrolled independently.

    Returns
    -------
    ProtectedWriterSubtreeObservation
        Stable sampled populated/frozen kernel bits and retained inode identity.
        Populated includes descendants, unlike a leader PID or cgroup.procs list.

    Raises
    ------
    ProtectedWriterSubtreeStateChanged
        On changing valid event snapshots with unchanged retained inode metadata.
    ValueError
        On a foreign filesystem, non-directory, changed metadata or malformed data.
    OSError
        On inaccessible descriptors or cgroup control files.

    Notes
    -----
    This performs I/O outside the authority actor. Even an empty subtree cannot
    establish quiescence if processes can migrate, be newly attached, or pass
    target descriptors outside it. Root-controlled launch/migration, complete
    descendant membership and descriptor isolation are separate mandatory gates.
    No signalling, freezing, process termination or cgroup mutation occurs here.
    """
    before = os.fstat(directory_fd)
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError("writer subtree descriptor is not a directory")
    with_fd = os.open("/sys/fs/cgroup", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if (
            before.st_dev != os.fstat(with_fd).st_dev
            or os.fstatvfs(directory_fd).f_fsid != os.fstatvfs(with_fd).f_fsid
        ):
            raise ValueError("writer subtree is not on the kernel cgroup filesystem")
    finally:
        os.close(with_fd)
    first = _read_events(directory_fd)
    controls = capture_protected_cgroup_controls(directory_fd)
    second = _read_events(directory_fd)
    after = os.fstat(directory_fd)
    if before != after:
        raise ValueError("writer subtree changed during observation")
    events = parse_protected_cgroup_events(first)
    parse_protected_cgroup_events(second)
    if first != second:
        raise ProtectedWriterSubtreeStateChanged("writer subtree changed during observation")
    return ProtectedWriterSubtreeObservation(
        before.st_dev,
        before.st_ino,
        before.st_uid,
        events.populated,
        events.frozen,
        controls,
    )


def _read_events(directory_fd: int) -> bytes:
    """Read bounded events through a retained directory and reject non-files."""
    descriptor = os.open("cgroup.events", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("writer subtree events are not a kernel file")
        return os.read(descriptor, 4097)
    finally:
        os.close(descriptor)


def capture_protected_writer_subtree(
    directory_fd: int | None = None,
) -> ProtectedWriterSubtreeObservation:
    """Bind the running writer's kernel membership to a retained subtree inode.

    Parameters
    ----------
    directory_fd:
        Independently supervisor-retained descriptor when supplied. Without it,
        open the actual current cgroup for diagnostic capture only; its identity
        must still match independently enrolled policy before boundary acceptance.

    Returns
    -------
    ProtectedWriterSubtreeObservation
        Recursive kernel facts for the exact current process membership.

    Raises
    ------
    ValueError
        On unsupported membership, another subtree or a concurrent migration.
    OSError
        On unavailable kernel membership or retained subtree descriptors.

    Notes
    -----
    A current populated subtree describes a live writer, never quiescence. This
    does not prove root-owned migration controls or absence of descriptor escape.
    """
    retained = None if directory_fd is None else os.dup(directory_fd)
    try:
        membership = _membership()
        path = parse_protected_cgroup_membership(membership)
        current = os.open(
            "/sys/fs/cgroup" + path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            target = current if retained is None else retained
            identity, expected = os.fstat(target), os.fstat(current)
            if (identity.st_dev, identity.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError("writer belongs to another process subtree")
            observation = inspect_protected_writer_subtree(target)
            if _membership() != membership:
                raise ValueError("writer cgroup membership changed during capture")
            return observation
        finally:
            os.close(current)
    finally:
        if retained is not None:
            os.close(retained)


def _membership() -> bytes:
    """Capture the current process cgroup membership within its byte budget."""
    with open("/proc/self/cgroup", "rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("writer cgroup membership exceeds read budget")
    return raw
