# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owned prerequisite backing and trusted readonly exposures
"""Copy only captured bytes into fresh root-owned Samsung files and pinned views."""

from __future__ import annotations

import ctypes
import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .capsule import CapturedCapsule

_STORAGE = Path("/media/anulum/GOTM")
_VIEW_PARENT = Path("/opt/synapse-channel/protected-runtime-qualification")
_EVIDENCE_PARENT = Path("/opt/synapse-channel/protected-runtime-qualification-evidence")

_RETIREMENT_STEPS = [
    "Owner only: confirm all campaign processes stopped. Absent or failed quiescence is a hold.",
    "Open the named backing without following links; compare device/inode "
    "with this record. Refuse a mismatch.",
    "Open the exact evidence target; compare mount ID, report device/inode "
    "and source path with this record. Refuse a mismatch.",
    "Unmount only that verified bind; verify its mount ID is absent, "
    "then remove its root-owned placeholder.",
    "If an instrumentation section exists, first retire its dataset bind under "
    "the separately recorded exact-identity procedure. Unknown exposure state is a hold.",
    "Recheck backing device/inode and stopped writers, then remove only "
    "that backing tree. Never remove role accounts.",
]


def measure_backing_tree(directory: int) -> dict[str, int]:
    """Measure actual retained files without reading their content or following links.

    Parameters
    ----------
    directory:
        Borrowed directory descriptor after every campaign writer has stopped.

    Returns
    -------
    dict[str, int]
        Logical bytes including directory entries, allocated bytes, file count
        and directory count observed in the retained backing tree.

    Raises
    ------
    ValueError
        On a nonregular object, device crossing or more than 20000 entries.
    OSError
        On an unavailable or changed actual filesystem object.
    """
    root = os.fstat(directory)
    total = {"size_bytes": 0, "allocated_bytes": 0, "files": 0, "directories": 0}
    _measure_entry(root, root.st_dev, total)
    with ExitStack() as resources:
        frame = resources.enter_context(ExitStack())
        current = os.open(
            ".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
        )
        frame.callback(os.close, current)
        entries = frame.enter_context(os.scandir(current))
        stack = [(current, entries, frame)]
        while stack:
            current, entries, frame = stack[-1]
            entry = next(entries, None)
            if entry is None:
                stack.pop()
                frame.close()
                continue
            child = entry.stat(follow_symlinks=False)
            _measure_entry(child, root.st_dev, total)
            if stat.S_ISDIR(child.st_mode):
                frame = resources.enter_context(ExitStack())
                descriptor = os.open(
                    entry.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=current,
                )
                frame.callback(os.close, descriptor)
                observed = os.fstat(descriptor)
                if (observed.st_dev, observed.st_ino) != (child.st_dev, child.st_ino):
                    raise ValueError("retained backing directory changed during traversal")
                stack.append((descriptor, frame.enter_context(os.scandir(descriptor)), frame))
    return total


def _measure_entry(metadata: os.stat_result, device: int, total: dict[str, int]) -> None:
    """Account for one streamed entry before another child can be enumerated."""
    if metadata.st_dev != device or total["files"] + total["directories"] >= 20000:
        raise ValueError("retained backing exceeds its filesystem or entry bound")
    if stat.S_ISDIR(metadata.st_mode):
        total["directories"] += 1
    elif stat.S_ISREG(metadata.st_mode):
        total["files"] += 1
    else:
        raise ValueError("retained backing has a nonregular or foreign file")
    total["size_bytes"] += metadata.st_size
    total["allocated_bytes"] += metadata.st_blocks * 512


def parse_descriptor_mount_id(fdinfo: str) -> int:
    """Parse one positive mount ID from descriptor observation text.

    Text validation alone does not establish kernel provenance or retain a file
    descriptor. The observer must read fdinfo from its own retained descriptor.
    """
    values = [
        line.partition(":")[2].strip() for line in fdinfo.splitlines() if line.startswith("mnt_id:")
    ]
    if len(values) != 1 or not values[0].isdecimal() or int(values[0]) <= 0:
        raise ValueError("retained descriptor has no exact kernel mount identity")
    return int(values[0])


def _descriptor_mount_id(descriptor: int) -> int:
    """Read one actual kernel mount identity from the retained descriptor."""
    return parse_descriptor_mount_id(Path(f"/proc/self/fdinfo/{descriptor}").read_text())


def describe_retained_attempt(backing: int, evidence: int, target: Path) -> dict[str, object]:
    """Describe an actual evidence bind and its retained source for operator recovery.

    Parameters
    ----------
    backing:
        Borrowed root backing directory descriptor retained by the producer.
    evidence:
        Borrowed regular report file descriptor within that backing directory.
    target:
        Absolute existing readonly report bind, inspected through a separate FD.

    Returns
    -------
    dict[str, object]
        Actual bind and backing identities, retention reason and ordered
        owner-run retirement procedure. This observation grants no deletion.

    Raises
    ------
    ValueError, OSError
        On changed source identity, missing readonly bind or invalid descriptors.

    Notes
    -----
    Size is measured separately after publication content has been determined.
    Neither this observation nor a component result authorises runtime execution.
    """
    from synapse_channel.core.protected_write_mounts import parse_protected_writer_mounts

    root = os.fstat(backing)
    source = os.fstat(evidence)
    if not stat.S_ISDIR(root.st_mode) or not stat.S_ISREG(source.st_mode):
        raise ValueError("retention requires an actual directory and regular report")
    backing_path = os.readlink(f"/proc/self/fd/{backing}")
    source_path = os.readlink(f"/proc/self/fd/{evidence}")
    if (
        not target.is_absolute()
        or ".." in target.parts
        or source_path != str(Path(backing_path) / "report.json")
        or not Path(backing_path).is_absolute()
    ):
        raise ValueError("retained report does not belong to its exact backing path")
    descriptor = os.open(target, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        observed = os.fstat(descriptor)
        mount_id = _descriptor_mount_id(descriptor)
        source_mount_id = _descriptor_mount_id(evidence)
        if (observed.st_dev, observed.st_ino) != (source.st_dev, source.st_ino):
            raise ValueError("retained evidence bind source identity differs")
        records = parse_protected_writer_mounts(Path("/proc/self/mountinfo").read_bytes())
        matches = [row for row in records if row.mount_id == mount_id and row.target == str(target)]
        if (
            mount_id == source_mount_id
            or len(matches) != 1
            or not {"ro", "nosuid", "nodev", "noexec"} <= matches[0].options
        ):
            raise ValueError("retained evidence is not the exact readonly bind")
    finally:
        os.close(descriptor)
    return {
        "reason": "operator recovery; retain until the owner verifies quiescence and retires it",
        "evidence_bind": {
            "mount_id": mount_id,
            "target": str(target),
            "source": source_path,
            "source_mount_id": source_mount_id,
            "source_device": source.st_dev,
            "source_inode": source.st_ino,
            "mount_root": matches[0].root,
        },
        "backing": {
            "path": backing_path,
            "device": root.st_dev,
            "inode": root.st_ino,
            "ancestry": describe_backing_ancestry(backing),
        },
        "retirement": list(_RETIREMENT_STEPS),
    }


def describe_backing_ancestry(directory: int) -> list[dict[str, object]]:
    """Observe actual retained ancestors, including their root-custody status.

    Parameters
    ----------
    directory:
        Borrowed actual backing directory descriptor.

    Returns
    -------
    list[dict[str, object]]
        Leaf-to-root paths, identities, owner/mode and root-control observations.
        An observation grants neither access denial nor host qualification.

    Raises
    ------
    ValueError, OSError
        If a retained ancestor is unavailable or the chain exceeds 256 entries.
    """
    result: list[dict[str, object]] = []
    current = os.dup(directory)
    try:
        for _depth in range(256):
            metadata = os.fstat(current)
            result.append(
                {
                    "path": os.readlink(f"/proc/self/fd/{current}"),
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                    "uid": metadata.st_uid,
                    "gid": metadata.st_gid,
                    "mode": stat.S_IMODE(metadata.st_mode),
                    "root_controlled": metadata.st_uid == metadata.st_gid == 0
                    and not metadata.st_mode & 0o022,
                }
            )
            parent = os.open(
                "..",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            observed = os.fstat(parent)
            os.close(current)
            current = parent
            if (metadata.st_dev, metadata.st_ino) == (observed.st_dev, observed.st_ino):
                return result
        raise ValueError("retained backing ancestry exceeds its depth bound")
    finally:
        os.close(current)


def read_effect_marker(directory: int) -> bytes:
    """Read one bounded regular marker through its retained data directory.

    Parameters
    ----------
    directory:
        Borrowed descriptor for the campaign's data directory, read only after
        the controller has independently confirmed writer quiescence.

    Returns
    -------
    bytes
        Complete marker content, at most 4096 bytes.

    Raises
    ------
    ValueError
        If the opened object is not regular or exceeds the marker byte bound.
    OSError
        If the fixed marker cannot be opened without following a symlink or read.
    """
    descriptor = os.open(
        "marker",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        dir_fd=directory,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("prerequisite marker is not a regular file")
        raw = bytearray()
        while len(raw) <= 4096:
            chunk = os.read(descriptor, 4097 - len(raw))
            if not chunk:
                return bytes(raw)
            raw.extend(chunk)
        raise ValueError("prerequisite marker exceeds its byte bound")
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    """Require complete writes to an owned file before sealing or exposing it."""
    view = memoryview(content)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written <= 0:
            raise OSError("root prerequisite copy made no write progress")
        offset += written


def open_directory(path: Path, *, trusted: bool, create: bool) -> int:
    """Retain canonical no-follow directory ancestry, optionally requiring root custody.

    Parameters
    ----------
    path:
        Canonical absolute directory path; descendant symlinks are refused.
    trusted:
        Require every opened component to be root UID/GID with no group/other write.
    create:
        Create missing components as root-owned directories after the root guard.

    Returns
    -------
    int
        Owned directory FD; caller must close it.

    Raises
    ------
    ValueError, OSError
        On unsafe ancestry, malformed path or inaccessible actual directories.
    PermissionError
        When creation is requested without root credentials.
    """
    if create:
        _require_root()
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("bootstrap directory must be canonical and absolute")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            os.close(current)
            current = child
            metadata = os.fstat(current)
            if trusted and (
                metadata.st_uid != 0 or metadata.st_gid != 0 or metadata.st_mode & 0o022
            ):
                raise ValueError("bootstrap executable exposure requires trusted root ancestry")
        return current
    except BaseException:
        os.close(current)
        raise


def _require_root() -> None:
    """Refuse unprivileged mutations before creating files or changing namespace views."""
    if os.geteuid() != 0 or os.getegid() != 0:
        raise PermissionError("prerequisite storage mutation requires root")


def _open_backing_parent(parent: Path) -> int:
    """Walk only below the actual Samsung mount, refusing nested device crossings."""
    relative = parent.relative_to(_STORAGE)
    if any(part in {".", ".."} for part in relative.parts):
        raise ValueError("invalid prerequisite backing ancestry")
    current = open_directory(_STORAGE, trusted=False, create=False)
    device = os.fstat(current).st_dev
    try:
        for part in relative.parts:
            try:
                os.mkdir(part, 0o755, dir_fd=current)
            except FileExistsError:
                pass
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            os.close(current)
            current = child
            if os.fstat(current).st_dev != device:
                raise ValueError("prerequisite backing escaped the approved storage device")
        return current
    except BaseException:
        os.close(current)
        raise


def create_backing(parent: Path, run_id: str) -> int:
    """Create a private pinned Samsung directory for fresh root-owned payload files.

    Parameters
    ----------
    parent:
        Approved absolute Samsung parent. Retained traversal refuses symlinks
        and any nested filesystem device crossing below the actual mount.
    run_id:
        Fresh ``run-`` identifier with 16 hexadecimal digits. Never reused.

    Returns
    -------
    int
        Owned FD for a fresh empty root-owned 0700 directory.

    Raises
    ------
    PermissionError
        Unless the actual effective UID/GID are root.
    ValueError, OSError
        On unavailable storage, unsafe ancestry, reused identifier or failed custody.
    """
    _require_root()
    if re.fullmatch("run-[0-9a-f]{16}", run_id) is None:
        raise ValueError("invalid prerequisite run identifier")
    if not os.path.ismount(_STORAGE):
        raise ValueError("approved Samsung storage mount is unavailable")
    parent_fd = _open_backing_parent(parent)
    try:
        os.mkdir(run_id, 0o700, dir_fd=parent_fd)
        result = os.open(
            run_id,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(result)
            if metadata.st_uid != 0 or metadata.st_mode & 0o077 or os.listdir(result):
                raise ValueError("prerequisite backing is not a private empty root directory")
            os.fchown(result, 0, 0)
            os.fchmod(result, 0o700)
            return result
        except BaseException:
            os.close(result)
            raise
    finally:
        os.close(parent_fd)


def write_payload(backing: int, capsule: CapturedCapsule) -> int:
    """Write fresh root-owned files, closing every writable descriptor before exposure.

    Parameters
    ----------
    backing:
        Borrowed root-retained private directory created by ``create_backing``.
    capsule:
        Complete hash-approved captured bytes. This function never reopens staging.

    Returns
    -------
    int
        Owned readonly payload-directory FD. Caller closes it after detaching its view.

    Raises
    ------
    PermissionError, OSError
        On non-root caller, reused entry, incomplete write or failed flush/ownership.

    Notes
    -----
    All payload inodes are new and root-owned under 0755 directories. Only the
    two fixed runtime loader/interpreter paths are 0555; other files are 0444.
    The privileged bootstrap must still install a readonly trusted view and
    reverify every byte before importing it. Partial files remain private on failure.
    """
    _require_root()
    os.mkdir("payload", 0o755, dir_fd=backing)
    payload = os.open(
        "payload",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=backing,
    )
    try:
        for name, content in capsule.content.items():
            _write_entry(payload, name, content)
        _write_entry(payload, "manifest.json", capsule.manifest.canonical_bytes())
        os.fsync(payload)
        return payload
    except BaseException:
        os.close(payload)
        raise


def _write_entry(root: int, name: str, content: bytes) -> None:
    """Create one already validated captured entry beneath an owned private tree."""
    current = os.dup(root)
    try:
        parts = name.split("/")
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o755, dir_fd=current)
            except FileExistsError:
                pass
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            os.close(current)
            current = child
        descriptor = os.open(
            parts[-1],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o400,
            dir_fd=current,
        )
        try:
            _write_all(descriptor, content)
            os.fchown(descriptor, 0, 0)
            # Only these two fixed, hash-approved native entry points are executable.
            # Keep this sealed bootstrap module independent of mutable staging imports.
            executable = name in {
                "runtime/native/lib/ld-linux-x86-64.so.2",
                "runtime/python/bin/python3.12",
            }
            os.fchmod(descriptor, 0o555 if executable else 0o444)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(current)
    finally:
        os.close(current)


def _require_exposure(target: Path) -> None:
    """Refuse alias paths and targets outside the two caller-owned exposure roots."""
    if (
        target.parent not in {_VIEW_PARENT, _EVIDENCE_PARENT}
        or re.fullmatch(r"run-[0-9a-f]{16}(?:\.json)?", target.name) is None
    ):
        raise ValueError("prerequisite exposure is outside the trusted namespace")
    parent = open_directory(target.parent, trusted=True, create=False)
    os.close(parent)
    metadata = target.lstat()
    if metadata.st_uid != 0 or metadata.st_gid != 0 or metadata.st_mode & 0o022:
        raise ValueError("prerequisite exposure is not a root-owned protected object")


def bind_readonly(source: int, target: Path, *, executable: bool = True) -> None:
    """Bind an owned retained source onto a trusted precreated target, then make it readonly.

    Parameters
    ----------
    source:
        Borrowed FD for fresh root-owned non-writable payload or evidence.
    target:
        Precreated root-owned target beneath either fixed trusted exposure root.
    executable:
        Keep code views executable by default. False also sets noexec for
        sealed measurement datasets; it never adds a writable exception.

    Raises
    ------
    PermissionError
        Unless the actual effective UID/GID are root.
    ValueError, OSError
        On foreign target, unsafe ancestry, incorrect ownership or failed native mount.

    Notes
    -----
    The new per-mount view is readonly, nosuid and nodev. Only code needs execution.
    On remount failure, detachment of this exact new view is attempted. If
    detachment also fails, both errors remain chained and the view may remain
    attached until its mount namespace is torn down (guest shutdown in native
    qualification). Treat that view as unqualified. This does not fence any
    previously held writable inode descriptor.
    """
    if type(executable) is not bool:
        raise ValueError("invalid prerequisite bind execution mode")
    _require_root()
    _require_exposure(target)
    expected = os.fstat(source)
    if (
        expected.st_uid != 0
        or expected.st_gid != 0
        or expected.st_mode & 0o022
        or not (stat.S_ISDIR(expected.st_mode) or stat.S_ISREG(expected.st_mode))
    ):
        raise ValueError("prerequisite source is not a protected root-owned object")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_char_p,
    ]
    libc.mount.restype = ctypes.c_int
    origin = os.fsencode(f"/proc/self/fd/{source}")
    destination = os.fsencode(target)
    ctypes.set_errno(0)
    if libc.mount(origin, destination, None, 4096, None) != 0:
        raise OSError(ctypes.get_errno(), "root prerequisite bind failed")
    attributes = 32 | 4096 | 1 | 2 | 4 | (0 if executable else 8)
    try:
        if libc.mount(None, destination, None, attributes, None) != 0:
            raise OSError(ctypes.get_errno(), "root prerequisite readonly bind failed")
        _verify_readonly_bind(source, target, executable=executable)
    except BaseException:
        unbind(target, expected_identity=(expected.st_dev, expected.st_ino))
        raise


def _verify_readonly_bind(source: int, target: Path, *, executable: bool) -> None:
    """Read fresh kernel mount state before any captured application code is imported."""
    descriptor = os.open(target, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        expected, observed = os.fstat(source), os.fstat(descriptor)
        mount_id = _descriptor_mount_id(descriptor)
        records = [
            line.split(" - ", 1)[0].split()
            for line in Path("/proc/self/mountinfo").read_text().splitlines()
        ]
        matches = [
            row
            for row in records
            if len(row) >= 6 and row[0] == str(mount_id) and row[4] == str(target)
        ]
        if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("readonly bind source identity differs after remount")
        if mount_id == _descriptor_mount_id(source) or len(matches) != 1:
            raise ValueError("readonly bind has no separate exact kernel mount identity")
        options = set(matches[0][5].split(","))
        if not {"ro", "nosuid", "nodev"} <= options or ("noexec" in options) == executable:
            raise ValueError("readonly bind mount options differ after remount")
    finally:
        os.close(descriptor)


def unbind(target: Path, *, expected_identity: tuple[int, int]) -> None:
    """Detach only a caller-owned trusted exposure after all owned processes terminate.

    Parameters
    ----------
    target:
        Exact trusted root-owned exposure established by ``bind_readonly``.
    expected_identity:
        Independently retained source device/inode pair, checked before detach.

    Raises
    ------
    PermissionError, ValueError, OSError
        On absent privilege, substituted exposure or refused native unmount.

    Notes
    -----
    Uses lazy detach only for this validated exposure. It neither kills processes
    nor proves quiescence. The controller must separately stop and reap every
    owned descendant before disposing of any backing files.
    """
    _require_root()
    _require_exposure(target)
    metadata = target.lstat()
    if (metadata.st_dev, metadata.st_ino) != expected_identity:
        raise ValueError("prerequisite exposure identity changed before cleanup")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.umount2.restype = ctypes.c_int
    ctypes.set_errno(0)
    if libc.umount2(os.fsencode(target), 2) != 0:
        raise OSError(ctypes.get_errno(), "root prerequisite exposure could not unmount")
