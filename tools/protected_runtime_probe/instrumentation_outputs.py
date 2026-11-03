# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — retained recording leaves and raw-byte sealing
"""Copy bounded recorded bytes by descriptor without parsing child databases."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass

from .capsule import require_posix_descriptors
from .instrumentation import RECORDING_ROLES
from .instrumentation_recording import MAX_RECORDING_BYTES, RECORDING_FILENAME


def _require_enrollment(role: str, descriptor: int, uid: int, gid: int) -> None:
    """Reject malformed role and numeric enrollment before a filesystem effect."""
    if role not in RECORDING_ROLES:
        raise ValueError("unexpected recording output role")
    if type(descriptor) is not int or descriptor < 0:
        raise ValueError("invalid recording output descriptor")
    if any(type(x) is not int or not 0 <= x < 2**32 - 1 for x in (uid, gid)):
        raise ValueError("invalid recording output owner")


@dataclass(frozen=True)
class RecordingOutput:
    """Independently retained directory and exact expected producer principal."""

    role: str
    descriptor: int
    uid: int
    gid: int
    identity: tuple[int, int]

    def __post_init__(self) -> None:
        """Require fixed role names and exact numeric descriptor/principal pins."""
        _require_enrollment(self.role, self.descriptor, self.uid, self.gid)
        if (
            type(self.identity) is not tuple
            or len(self.identity) != 2
            or any(type(x) is not int or x < 0 for x in self.identity)
            or self.identity[1] == 0
        ):
            raise ValueError("invalid recording output directory identity")


def _private_directory(descriptor: int, uid: int, gid: int) -> os.stat_result:
    """Check one borrowed private directory against its independently selected owner."""
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or (info.st_uid, info.st_gid) != (uid, gid)
    ):
        raise PermissionError("recording directory is not private to its enrolled owner")
    return info


def create_recording_output(parent: int, role: str, uid: int, gid: int) -> RecordingOutput:
    """Create one fresh role-owned leaf under a retained private parent.

    Parameters
    ----------
    parent:
        Borrowed 0700 directory owned by the actual caller. The root campaign
        additionally requires root context before invoking this primitive.
    role:
        One fixed recording role, also the exact new leaf name.
    uid, gid:
        Independently enrolled final producer identity.

    Returns
    -------
    RecordingOutput
        Owned directory descriptor. Its caller must close it after final sealing.

    Raises
    ------
    ValueError, OSError
        On invalid enrollment, reused names or failed native creation/ownership.
    PermissionError
        Unless the actual caller owns the private parent.

    Notes
    -----
    Creates no process, role account, namespace, mount or execution grant.
    Ordinary tests may create only their own leaves; that proves no root custody.
    """
    require_posix_descriptors(os.name)
    _require_enrollment(role, parent, uid, gid)
    _private_directory(parent, os.geteuid(), os.getegid())
    os.mkdir(role, 0o700, dir_fd=parent)
    descriptor = os.open(
        role, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
    )
    try:
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o700)
        info = _private_directory(descriptor, uid, gid)
        return RecordingOutput(role, descriptor, uid, gid, (info.st_dev, info.st_ino))
    except BaseException:
        os.close(descriptor)
        raise


def _file_pin(info: os.stat_result) -> tuple[int, ...]:
    """Exclude only read-induced access time from a stable byte-copy observation."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _dataset_metadata(descriptor: int, output: RecordingOutput) -> os.stat_result:
    """Admit raw bounded regular bytes, never a child-selected reader or path."""
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or not 0 < info.st_size <= MAX_RECORDING_BYTES
        or stat.S_IMODE(info.st_mode) != 0o600
        or (info.st_uid, info.st_gid) != (output.uid, output.gid)
        or info.st_dev != output.identity[0]
    ):
        raise ValueError("recording dataset metadata differs from its enrollment")
    return info


def _copy_raw_dataset(source: int, target: int, expected: os.stat_result) -> str:
    """Copy exactly the observed raw length and refuse a changed or partial file."""
    digest = hashlib.sha256()
    remaining = expected.st_size
    while remaining:
        raw = os.read(source, min(65536, remaining))
        if not raw:
            raise ValueError("recording dataset ended before its enrolled length")
        if os.write(target, raw) != len(raw):
            raise OSError("sealed recording write was incomplete")
        digest.update(raw)
        remaining -= len(raw)
    if os.read(source, 1) or _file_pin(os.fstat(source)) != _file_pin(expected):
        raise ValueError("recording dataset changed during sealing")
    os.fsync(target)
    return digest.hexdigest()


def seal_recording_output(
    output: RecordingOutput, destination: int, *, host_pid: int
) -> dict[str, str | int]:
    """Seal one enrolled producer's raw dataset after independent quiescence.

    Parameters
    ----------
    output:
        Retained private leaf and exact independently enrolled owner/inode.
    destination:
        Borrowed private output directory owned by the actual sealer. The
        root campaign owns this directory and its ancestry separately.
    host_pid:
        Actual host PID retained/observed by the parent, never child diagnostics.

    Returns
    -------
    dict[str, str | int]
        Byte hash, exact size, producer identity and source/copy inode pins.

    Raises
    ------
    ValueError, OSError
        On a changed leaf, any sidecar, invalid metadata, reused copy or failed write.
    PermissionError
        On incorrect ownership or private directory custody.

    Notes
    -----
    The caller must independently prove all writers quiescent. This primitive
    does not prove quiescence or validate SQLite/coverage contents. No parser,
    import, child configuration or child-selected filename is executed.
    Both original and copy become readonly and owned by the actual sealer.
    Failure retains any partial copy for the failed campaign's recovery record.
    """
    require_posix_descriptors(os.name)
    if type(host_pid) is not int or host_pid <= 0:
        raise ValueError("recording requires an independently observed host PID")
    actual = _private_directory(output.descriptor, output.uid, output.gid)
    if (actual.st_dev, actual.st_ino) != output.identity:
        raise ValueError("recording output directory identity changed")
    _private_directory(destination, os.geteuid(), os.getegid())
    if os.listdir(output.descriptor) != [RECORDING_FILENAME]:
        raise ValueError("recording output has an absent dataset or unexpected sidecar")
    source = os.open(
        RECORDING_FILENAME,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        dir_fd=output.descriptor,
    )
    try:
        before = _dataset_metadata(source, output)
        return _seal_dataset_copy(source, destination, output, before, host_pid)
    finally:
        os.close(source)


def _seal_dataset_copy(
    source: int,
    destination: int,
    output: RecordingOutput,
    before: os.stat_result,
    host_pid: int,
) -> dict[str, str | int]:
    """Commit only one complete descriptor-captured copy and close its writable FD."""
    name = output.role + ".sqlite"
    target = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=destination,
    )
    try:
        digest = _copy_raw_dataset(source, target, before)
        if os.listdir(output.descriptor) != [RECORDING_FILENAME]:
            raise ValueError("recording output inventory changed during sealing")
        os.fchown(source, os.geteuid(), os.getegid())
        os.fchmod(source, 0o400)
        os.fchown(output.descriptor, os.geteuid(), os.getegid())
        os.fchmod(output.descriptor, 0o500)
        os.fchmod(target, 0o444)
        sealed = os.fstat(target)
        if sealed.st_size != before.st_size or sealed.st_nlink != 1:
            raise ValueError("sealed recording copy metadata changed")
        return {
            "role": output.role,
            "filename": name,
            "host_pid": host_pid,
            "producer_uid": output.uid,
            "producer_gid": output.gid,
            "sha256": digest,
            "size_bytes": before.st_size,
            "source_device": before.st_dev,
            "source_inode": before.st_ino,
            "sealed_device": sealed.st_dev,
            "sealed_inode": sealed.st_ino,
            "sealed_uid": sealed.st_uid,
            "sealed_gid": sealed.st_gid,
        }
    finally:
        os.close(target)
