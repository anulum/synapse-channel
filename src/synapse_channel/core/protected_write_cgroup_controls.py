# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained cgroup control ancestry observations
"""Observe actual cgroup control owners from a retained subtree to the kernel root."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass

from synapse_channel.core.protected_write_cgroup_parsing import parse_protected_cgroup_type


@dataclass(frozen=True)
class ProtectedCgroupObject:
    """Retained kernel inode permissions, not an enrollment declaration."""

    device: int
    inode: int
    uid: int
    gid: int
    mode: int


@dataclass(frozen=True)
class ProtectedCgroupControlLevel:
    """One retained directory and its migration/delegation control files."""

    directory: ProtectedCgroupObject
    controls: tuple[tuple[str, ProtectedCgroupObject], ...]
    kind: str


def capture_protected_cgroup_controls(
    directory_fd: int,
    *,
    max_depth: int = 64,
) -> tuple[ProtectedCgroupControlLevel, ...]:
    """Observe bounded control ancestry through retained parent descriptors.

    Parameters
    ----------
    directory_fd:
        Independently retained cgroup v2 subtree directory.
    max_depth:
        Positive ancestor budget including subtree and kernel cgroup root.

    Returns
    -------
    tuple[ProtectedCgroupControlLevel, ...]
        Ordered subtree-to-root metadata. No names or control content are changed.

    Raises
    ------
    ValueError
        On budget exhaustion, foreign filesystem, invalid controls or changed inode.
    OSError
        On inaccessible or removed cgroups/control files.

    Notes
    -----
    Run outside the authority actor. The trusted namespace's /sys/fs/cgroup
    anchors the terminal inode. The supervisor must bind this namespace and
    retain its launch/migration boundary independently. This is not proof that
    no target descriptor escaped or that writer descendants have terminated.
    """
    if type(max_depth) is not int or not 0 < max_depth <= 256:
        raise ValueError("invalid cgroup ancestry budget")
    current = os.dup(directory_fd)
    root: int | None = None
    try:
        root = os.open("/sys/fs/cgroup", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        anchor = os.fstat(root)
        levels: list[ProtectedCgroupControlLevel] = []
        for _ in range(max_depth):
            before = os.fstat(current)
            if (
                not stat.S_ISDIR(before.st_mode)
                or before.st_dev != anchor.st_dev
                or os.fstatvfs(current).f_fsid != os.fstatvfs(root).f_fsid
            ):
                raise ValueError("cgroup controls are outside the kernel filesystem")
            terminal = (before.st_dev, before.st_ino) == (anchor.st_dev, anchor.st_ino)
            names: tuple[str, ...] = (
                "cgroup.procs",
                "cgroup.threads",
                "cgroup.subtree_control",
            )
            if not terminal:
                names += ("cgroup.freeze", "cgroup.type")
            controls = []
            kind = "root" if terminal else ""
            for name in names:
                control = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current)
                try:
                    info = os.fstat(control)
                    if not stat.S_ISREG(info.st_mode) or info.st_dev != anchor.st_dev:
                        raise ValueError("invalid kernel cgroup control file")
                    controls.append((name, _object(info)))
                    if name == "cgroup.type":
                        kind = parse_protected_cgroup_type(os.read(control, 129))
                finally:
                    os.close(control)
            if os.fstat(current) != before:
                raise ValueError("cgroup control directory changed during capture")
            levels.append(ProtectedCgroupControlLevel(_object(before), tuple(controls), kind))
            if terminal:
                return tuple(levels)
            parent = os.open("..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = parent
        raise ValueError("cgroup control ancestry exceeds budget")
    finally:
        os.close(current)
        if root is not None:
            os.close(root)


def verify_protected_cgroup_controls(
    levels: tuple[ProtectedCgroupControlLevel, ...],
) -> None:
    """Refuse agent ownership or writable migration/delegation controls.

    Parameters
    ----------
    levels:
        Trusted retained result of actual complete control ancestry capture.
        Never a caller-deserialised chain or a replacement for namespace enrollment.

    Raises
    ------
    ValueError
        On absent controls or any non-root owner/group or group/world write mode.

    Notes
    -----
    This pure predicate requires every observed directory/control to be owned
    by root with no group/world write permissions and ordinary domain type.
    Threaded domains can separate thread membership and are refused. The exact
    kernel root has no cgroup.type file and is represented separately. See
    https://docs.kernel.org/admin-guide/cgroup-v2.html#core-interface-files.
    This does not terminate a
    writer, certify quiescence, or prove descriptor isolation.
    """
    if not levels:
        raise ValueError("cgroup control ancestry is unavailable")
    for index, level in enumerate(levels):
        if level.kind != ("root" if index == len(levels) - 1 else "domain"):
            raise ValueError("writer cgroup control ancestry is not an ordinary domain")
        for item in (level.directory, *(info for _name, info in level.controls)):
            if item.uid != 0 or item.gid != 0 or item.mode & 0o022:
                raise ValueError("cgroup migration or delegation controls are agent-accessible")


def _object(info: os.stat_result) -> ProtectedCgroupObject:
    """Retain inode, ownership and permission bits from one stat observation."""
    return ProtectedCgroupObject(
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
    )
