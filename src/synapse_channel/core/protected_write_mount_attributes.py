# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private writer namespace mount restrictions
"""Apply data-only mount attributes in an already isolated root-stage child."""

from __future__ import annotations

import ctypes
import os
import platform
import stat
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from synapse_channel.core.protected_write_kernel_boundary import (
    ProtectedWriterKernelEvidence,
    capture_protected_writer_kernel_evidence,
)
from synapse_channel.core.protected_write_mounts import verify_protected_writer_mounts

_MOUNT_SETATTR_NUMBER = 442
_AT_EMPTY_PATH = 0x1000
_AT_RECURSIVE = 0x8000
_MOUNT_ATTR_RDONLY = 0x00000001
_DATA_ONLY_ATTRIBUTES = 0x00000002 | 0x00000004 | 0x00000008
_MS_PRIVATE = 1 << 18
_MAX_WRITABLE_MOUNTS = 64


class _MountAttributes(ctypes.Structure):
    """Zero-initialized version-zero linux/mount.h mount_attr layout."""

    _fields_ = [
        ("attr_set", ctypes.c_uint64),
        ("attr_clr", ctypes.c_uint64),
        ("propagation", ctypes.c_uint64),
        ("userns_fd", ctypes.c_uint64),
    ]


def require_protected_writer_mount_abi(
    observed_platform: str, machine: str, pointer_bytes: int
) -> None:
    """Validate observed ABI values before using the native mount backend.

    Parameters
    ----------
    observed_platform, machine:
        Platform and machine names supplied by the actual host adapter.
    pointer_bytes:
        Actual native pointer width in bytes.

    Raises
    ------
    OSError
        Unless the observations identify supported 64-bit native Linux.
    """
    if observed_platform != "linux" or machine not in {"x86_64", "aarch64"} or pointer_bytes != 8:
        raise OSError("writer mount restrictions require supported native Linux")


def _require_native_mount_backend() -> None:
    """Pass actual host observations before namespace or target access."""
    require_protected_writer_mount_abi(
        sys.platform, platform.machine(), ctypes.sizeof(ctypes.c_void_p)
    )


def _require_namespace_pin(identity: tuple[int, int]) -> None:
    """Require an immutable independently retained device/inode namespace pin."""
    if (
        type(identity) is not tuple
        or len(identity) != 2
        or type(identity[0]) is not int
        or identity[0] < 0
        or type(identity[1]) is not int
        or identity[1] <= 0
    ):
        raise ValueError("invalid independently enrolled host namespace identity")


def _require_private_root_stage(host_mount_namespace: tuple[int, int]) -> tuple[int, int]:
    """Refuse unsupported, shared, threaded or host-namespace mutation contexts."""
    _require_native_mount_backend()
    if os.getresuid() != (0, 0, 0):
        raise PermissionError("writer mount restrictions require the trusted root-stage child")
    _require_namespace_pin(host_mount_namespace)
    current = Path("/proc/self/ns/mnt").stat()
    identity = (current.st_dev, current.st_ino)
    if identity == host_mount_namespace:
        raise ValueError("writer mount restrictions refuse the retained host namespace")
    if len(tuple(Path("/proc/self/task").iterdir())) != 1:
        raise ValueError("writer mount restrictions require a single-threaded root stage")
    return identity


def _require_writer_credentials(writer_uid: int, writer_gid: int, shared_uid: int) -> None:
    """Require independently enrolled dedicated writer credentials before mutation."""
    if (
        any(type(value) is not int or value < 0 for value in (writer_uid, writer_gid, shared_uid))
        or writer_uid in {0, 1000, shared_uid}
        or writer_gid == 0
    ):
        raise ValueError("invalid dedicated writer mount credentials")


def _require_writable_mount_targets(targets: Mapping[str, int]) -> None:
    """Bound exact mountpoint declarations before observing borrowed descriptors."""
    if not 0 < len(targets) <= _MAX_WRITABLE_MOUNTS:
        raise ValueError("invalid writer writable mount count")
    for path, descriptor in targets.items():
        if (
            type(path) is not str
            or not path.startswith("/")
            or path == "/"
            or str(PurePosixPath(path)) != path
            or ".." in PurePosixPath(path).parts
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or type(descriptor) is not int
            or descriptor < 0
        ):
            raise ValueError("invalid writer writable mount enrollment")


def _require_target_directory(path: str, descriptor: int, uid: int, gid: int) -> None:
    """Bind a private writer leaf to retained identity under trusted root ancestors."""
    current = os.fstat(descriptor)
    linked = Path(path).lstat()
    if (
        not stat.S_ISDIR(current.st_mode)
        or not stat.S_ISDIR(linked.st_mode)
        or (current.st_dev, current.st_ino) != (linked.st_dev, linked.st_ino)
        or current.st_uid != uid
        or current.st_gid != gid
        or stat.S_IMODE(current.st_mode) != 0o700
    ):
        raise ValueError("writer mount target identity or private ownership differs")
    for parent in Path(path).parents:
        metadata = parent.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_mode & 0o022
        ):
            raise ValueError("writer mount target ancestors must remain root owned")


def inspect_protected_writer_mount_targets(
    targets: Mapping[str, int], *, owner_uid: int, owner_gid: int
) -> ProtectedWriterKernelEvidence:
    """Observe exact current-namespace mountpoints without changing their attributes.

    Parameters
    ----------
    targets:
        Canonical mountpoint paths mapped to borrowed directory descriptors in
        the current namespace, bounded to 64 entries. A subdirectory descriptor
        is refused even if it resides on the same mounted filesystem.
    owner_uid, owner_gid:
        Expected actual ownership of each 0700 leaf. Diagnostic observations
        may use the current shared UID; this does not enroll a dedicated writer.

    Returns
    -------
    ProtectedWriterKernelEvidence
        Actual process/mount/root observations, never execution authority.

    Raises
    ------
    ValueError
        On invalid declarations, non-mountpoint descriptors, changed identity,
        unsafe ownership or writable/non-root ancestors.
    OSError
        On unsupported native Linux or inaccessible actual kernel/target state.

    Notes
    -----
    Performs no mount, permission, directory or process mutation. This exact
    binding check is used before and after root-stage attribute changes.
    It does not verify the whole mount policy, credentials, private namespace,
    installed code or exclusion of outside holders. Borrowed FDs stay open.
    """
    _require_native_mount_backend()
    if any(type(value) is not int or value < 0 for value in (owner_uid, owner_gid)):
        raise ValueError("invalid mount target observation credentials")
    targets = dict(targets)
    _require_writable_mount_targets(targets)
    observed = capture_protected_writer_kernel_evidence(targets)
    mounts = {mount.mount_id: mount for mount in observed.mounts}
    for root in observed.roots:
        # Changing attributes through a subdirectory FD would change the WHOLE
        # containing mount. Require its actual mountpoint, not just an inode.
        if mounts[root.mount_id].target != root.name:
            raise ValueError("writer descriptor does not identify its exact mountpoint")
        _require_target_directory(root.name, targets[root.name], owner_uid, owner_gid)
    return observed


def _set_mount_attributes(
    descriptor: int, *, recursive: bool, set_flags: int, clear_flags: int, propagation: int
) -> None:
    """Apply native per-mount attributes through an exact retained descriptor."""
    attributes = _MountAttributes(set_flags, clear_flags, propagation, 0)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    ctypes.set_errno(0)
    result = libc.syscall(
        ctypes.c_long(_MOUNT_SETATTR_NUMBER),
        ctypes.c_int(descriptor),
        ctypes.c_char_p(b""),
        ctypes.c_uint(_AT_EMPTY_PATH | (_AT_RECURSIVE if recursive else 0)),
        ctypes.byref(attributes),
        ctypes.c_size_t(ctypes.sizeof(attributes)),
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, "writer namespace mount attribute change failed")


def apply_protected_writer_mount_restrictions(
    *,
    host_mount_namespace: tuple[int, int],
    writable_mount_descriptors: Mapping[str, int],
    writer_uid: int,
    writer_gid: int,
    shared_uid: int,
) -> ProtectedWriterKernelEvidence:
    """Make an existing private namespace read-only except exact data-only mounts.

    Parameters
    ----------
    host_mount_namespace:
        Independently retained original host device/inode pin, captured before
        namespace construction and validated by the trusted host wrapper against
        the supervisor's retained reference. That reference stays outside this
        child. The pin must differ from the current namespace; never derive it
        from an untrusted request or a namespace selected inside the sandbox.
    writable_mount_descriptors:
        Exact canonical namespace mountpoints mapped to borrowed directory FDs
        opened in THIS namespace, with at most 64 entries. Each leaf must be
        writer-owned 0700 beneath root-owned non-writable ancestors. Every FD
        must name the mountpoint itself, not a directory within another mount.
    writer_uid, writer_gid:
        Enrolled dedicated non-root filesystem credentials for all data leaves.
    shared_uid:
        Shared agent UID, independently enrolled and distinct from the writer.

    Returns
    -------
    ProtectedWriterKernelEvidence
        Actual ROOT-STAGE observations after exact mount policy verification.
        These credentials are still root; this result is never writer authority
        or an accepted post-drop kernel profile.

    Raises
    ------
    PermissionError
        Unless all real/effective/saved UIDs are root.
    ValueError
        On an invalid, host/shared/threaded namespace, unsafe enrollment,
        non-mountpoint FD, changed roots or a failed resulting mount policy.
    OSError
        On unsupported native Linux, inaccessible kernel data or mount_setattr
        refusal. There is no legacy remount or unprivileged fallback.

    Notes
    -----
    Use only in a disposable trusted ROOT-STAGE CHILD, after namespaces and
    mountpoints were independently constructed, before credential/capability
    drop and execution. Host namespace custody, root-installed code, blocked
    admission, exclusive descriptors and outside-writer exclusion are caller
    prerequisites. This API creates no account, directory, mount or service.

    The existing namespace must have exactly one base root and unique mount
    targets throughout its complete mount table. Its trusted constructor must
    establish that topology before this call; this setter does not flatten
    inherited mount stacks or construct the writer namespace.

    First recursively set private propagation and the read-only PER-MOUNT
    attribute on the namespace root and all submounts. Then clear read-only
    only on exact enrolled data mounts, setting nosuid/nodev/noexec. Filesystem
    superblock flags and underlying bytes/ownership are not changed. A writable
    alternate host view or outside FD remains unaffected, never fenced by this.

    On any failure, the child must abort; its private namespace may be partly
    restricted and is not rolled back. Borrowed descriptors remain caller owned.
    The supervisor must independently qualify the post-drop process/cgroup,
    syscall/FD custody, complete immutable installation and actual effects.
    """
    namespace = _require_private_root_stage(host_mount_namespace)
    targets = dict(writable_mount_descriptors)
    _require_writer_credentials(writer_uid, writer_gid, shared_uid)
    before = inspect_protected_writer_mount_targets(
        targets, owner_uid=writer_uid, owner_gid=writer_gid
    )
    root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        _set_mount_attributes(
            root,
            recursive=True,
            set_flags=_MOUNT_ATTR_RDONLY,
            clear_flags=0,
            propagation=_MS_PRIVATE,
        )
        for descriptor in targets.values():
            _set_mount_attributes(
                descriptor,
                recursive=False,
                set_flags=_DATA_ONLY_ATTRIBUTES,
                clear_flags=_MOUNT_ATTR_RDONLY,
                propagation=0,
            )
        after = inspect_protected_writer_mount_targets(
            targets, owner_uid=writer_uid, owner_gid=writer_gid
        )
        if after.mount_namespace != namespace or after.roots != before.roots:
            raise ValueError("writer mount namespace or retained roots changed")
        verify_protected_writer_mounts(after.mounts, writable_targets=frozenset(targets))
        return after
    finally:
        os.close(root)
