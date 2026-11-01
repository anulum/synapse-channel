# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected writer mount restrictions
"""Parse actual Linux mount tables and require an exact private writable domain."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_ESCAPES = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}
_MAX_BYTES = 1_048_576


@dataclass(frozen=True)
class ProtectedWriterMount:
    """One immutable kernel mount record with decoded paths and access flags."""

    mount_id: int
    parent_id: int
    device: tuple[int, int]
    root: str
    target: str
    options: frozenset[str]
    propagation: tuple[str, ...]
    filesystem: str


def _path(raw: str, *, kernel_escaped: bool = True) -> str:
    """Decode the kernel's four mount-path escapes without normalising aliases."""
    decoded = raw
    if kernel_escaped:
        for escape in re.findall(r"\\([^\\]{0,3})", raw):
            if escape not in _ESCAPES:
                raise ValueError("unsupported kernel mount path escape")
        decoded = re.sub(r"\\([0-7]{3})", lambda match: _ESCAPES[match[1]], raw)
    if (
        not decoded.startswith("/")
        or str(PurePosixPath(decoded)) != decoded
        or ".." in PurePosixPath(decoded).parts
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    ):
        raise ValueError("noncanonical kernel mount path")
    return decoded


def _mount_root(raw: str, filesystem: str) -> str:
    """Retain nsfs kernel inode labels separately from filesystem root paths."""
    if filesystem == "nsfs" and re.fullmatch(
        r"(?:mnt|net|pid|user|uts|ipc|cgroup|time):\[[1-9][0-9]*\]", raw
    ):
        return raw
    return _path(raw)


def _parse_mount_flags(
    raw_options: str, raw_propagation: list[str]
) -> tuple[frozenset[str], tuple[str, ...]]:
    """Require unambiguous access mode and closed kernel propagation fields."""
    options = raw_options.split(",")
    if "" in options or len(set(options)) != len(options) or len(set(options) & {"ro", "rw"}) != 1:
        raise ValueError("ambiguous kernel mount access")
    propagation = tuple(raw_propagation)
    if any(
        item != "unbindable"
        and re.fullmatch(r"(?:shared|master|propagate_from):[1-9][0-9]*", item) is None
        for item in propagation
    ) or len(set(propagation)) != len(propagation):
        raise ValueError("unsupported kernel mount propagation")
    return frozenset(options), propagation


def _parse_mount_record(line: str, seen: set[int]) -> ProtectedWriterMount:
    """Decode one record and retain snapshot-wide unique IDs before flag/path checks."""
    left, separator, right = line.partition(" - ")
    fields, tail = left.split(" "), right.split(" ")
    if not separator or len(fields) < 6 or len(tail) != 3 or "" in fields + tail:
        raise ValueError("invalid kernel mount record")
    ids = fields[:2]
    device = fields[2].split(":")
    if (
        len(device) != 2
        or any(not part.isascii() or not part.isdecimal() for part in ids + device)
        or any(int(part) <= 0 for part in ids)
    ):
        raise ValueError("invalid kernel mount identity")
    mount_id, parent_id = map(int, ids)
    if mount_id in seen:
        raise ValueError("duplicate kernel mount identity")
    seen.add(mount_id)
    options, propagation = _parse_mount_flags(fields[5], fields[6:])
    return ProtectedWriterMount(
        mount_id,
        parent_id,
        (int(device[0]), int(device[1])),
        _mount_root(fields[3], tail[0]),
        _path(fields[4]),
        options,
        propagation,
        tail[0],
    )


def parse_protected_writer_mounts(raw: bytes) -> tuple[ProtectedWriterMount, ...]:
    """Parse a bounded full mountinfo snapshot, refusing ambiguous records.

    Parameters
    ----------
    raw:
        Exact bytes read from the writer's own /proc/self/mountinfo.

    Returns
    -------
    tuple[ProtectedWriterMount, ...]
        Immutable records in kernel order; parsing grants no isolation authority.

    Raises
    ------
    ValueError
        On malformed, empty, oversized, duplicate-ID or ambiguous mount data.

    Notes
    -----
    Kernel paths use octal escapes. Control-character paths refuse; spaces and
    literal backslashes are retained exactly. Mount access uses per-mount flags,
    rather than superblock flags: a read-only bind may share a writable filesystem.
    Namespace filesystem roots retain their kernel name/inode labels; mount
    targets remain canonical absolute paths. See Linux fs/nsfs.c, ns_dname.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_BYTES:
        raise ValueError("invalid writer mount table budget")
    mounts = []
    seen: set[int] = set()
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
        for line in lines:
            mounts.append(_parse_mount_record(line, seen))
    except UnicodeError as failure:
        raise ValueError("invalid kernel mount encoding") from failure
    return tuple(mounts)


def _validate_writable_mount_enrollment(writable_targets: frozenset[str]) -> None:
    """Require immutable canonical exact write targets with no writable base root."""
    if (
        type(writable_targets) is not frozenset
        or any(
            type(path) is not str or _path(path, kernel_escaped=False) != path
            for path in writable_targets
        )
        or "/" in writable_targets
    ):
        raise ValueError("invalid writer writable mount enrollment")


def verify_protected_writer_mounts(
    mounts: tuple[ProtectedWriterMount, ...], *, writable_targets: frozenset[str]
) -> None:
    """Require a read-only base, private propagation and exact writable mounts.

    Parameters
    ----------
    mounts:
        Trusted records parsed from the complete current namespace mount table.
    writable_targets:
        Owner-enrolled exact mountpoints for target data and private journal/runtime
        state. No prefix inheritance; the base root can never be writable.

    Raises
    ------
    ValueError
        On invalid enrollment, writable base, shared propagation, duplicate targets,
        unenrolled writable mounts or missing nosuid/nodev/noexec restrictions.

    Notes
    -----
    A passing check is only the mount-table portion of isolation. Root descriptor
    identity, distinct UID, sealed credentials, pinned code, syscall policy and
    outside-namespace exclusion remain mandatory. No mount or owner is changed.
    """
    _validate_writable_mount_enrollment(writable_targets)
    targets = [mount.target for mount in mounts]
    if not mounts or len(set(targets)) != len(targets) or targets.count("/") != 1:
        raise ValueError("writer mount table lacks an unambiguous base")
    root = next(mount for mount in mounts if mount.target == "/")
    if "ro" not in root.options:
        raise ValueError("writer base mount is writable")
    if any(any(item != "unbindable" for item in mount.propagation) for mount in mounts):
        raise ValueError("writer mount propagation is not private")
    writable = frozenset(mount.target for mount in mounts if "rw" in mount.options)
    if writable != writable_targets:
        raise ValueError("writer writable mounts differ from exact enrollment")
    if any(
        "rw" in mount.options and not {"nosuid", "nodev", "noexec"} <= mount.options
        for mount in mounts
    ):
        raise ValueError("writer writable mount lacks data-only restrictions")
