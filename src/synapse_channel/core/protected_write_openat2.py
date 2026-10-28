# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Linux protected path resolution
"""Fail-closed openat2 resolution beneath an enrolled root descriptor.

The syscall profile requires native 64-bit x86_64 or aarch64 userspace.
"""

from __future__ import annotations

import ctypes
import os
import platform
import sys

_RESOLVE_NO_XDEV = 0x01
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_RESOLVE_FLAGS = _RESOLVE_NO_XDEV | _RESOLVE_NO_SYMLINKS | _RESOLVE_BENEATH
_OPENAT2_NUMBER = 437
_SUPPORTED_ARCHITECTURES = frozenset({"x86_64", "aarch64"})
_MAX_DESCRIPTOR = (1 << (ctypes.sizeof(ctypes.c_int) * 8 - 1)) - 1


class _OpenHow(ctypes.Structure):
    """The version-zero Linux open_how layout with zeroed extension bytes."""

    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


def _open_beneath(root_descriptor: int, relative_path: str, *, directory: bool) -> int:
    """Open a read-only path reference, refusing unsupported Linux hosts."""
    if (
        sys.platform != "linux"
        or platform.machine() not in _SUPPORTED_ARCHITECTURES
        or ctypes.sizeof(ctypes.c_void_p) != 8
    ):
        raise OSError("protected openat2 requires a supported Linux architecture")
    flags = os.O_PATH | os.O_CLOEXEC | (os.O_DIRECTORY if directory else 0)
    how = _OpenHow(flags, 0, _RESOLVE_FLAGS)
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    result = syscall(
        ctypes.c_long(_OPENAT2_NUMBER),
        ctypes.c_int(root_descriptor),
        ctypes.c_char_p(os.fsencode(relative_path)),
        ctypes.byref(how),
        ctypes.c_size_t(ctypes.sizeof(how)),
    )
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), relative_path)
    return int(result)


def _validate_path_descriptors(
    root_descriptor: int,
    relative_path: str,
    parent_descriptor: int,
    object_descriptor: int | None,
) -> None:
    """Validate the descriptor/path representation before invoking the Linux ABI."""
    if (
        type(root_descriptor) is not int
        or not 0 <= root_descriptor <= _MAX_DESCRIPTOR
        or type(parent_descriptor) is not int
        or not 0 <= parent_descriptor <= _MAX_DESCRIPTOR
        or (
            object_descriptor is not None
            and (
                type(object_descriptor) is not int or not 0 <= object_descriptor <= _MAX_DESCRIPTOR
            )
        )
        or type(relative_path) is not str
        or not relative_path
        or relative_path.startswith("/")
        or "\x00" in relative_path
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        raise ValueError("invalid protected openat2 path or descriptor")


def verify_protected_openat2_path(
    root_descriptor: int,
    relative_path: str,
    parent_descriptor: int,
    object_descriptor: int | None,
) -> None:
    """Match kernel-resolved parent and leaf to retained descriptors.

    Parameters
    ----------
    root_descriptor : int
        Borrowed enrolled directory descriptor in the nonnegative C int range.
    relative_path : str
        Nonempty relative path without empty, dot, parent or NUL components.
    parent_descriptor : int
        Retained parent whose device and inode must match kernel resolution.
    object_descriptor : int or None
        Retained leaf to compare, or None to require an actually absent leaf.

    Raises
    ------
    ValueError
        If inputs are ambiguous, a retained identity differs, or a leaf appears
        where the caller requires absence.
    OSError
        If native 64-bit Linux support is unavailable, a path crosses a mount
        or symlink, escapes the root, or an expected object cannot be resolved.

    Notes
    -----
    Every opened reference is closed, including refusal paths; borrowed
    descriptors remain owned by the caller. No fallback opener, target read,
    write or filesystem mutation occurs. Root custody and exclusion of
    concurrent writers remain separate gates. These comparisons cannot make
    mutable multi-component topology atomic against an unconfined writer.
    """
    _validate_path_descriptors(root_descriptor, relative_path, parent_descriptor, object_descriptor)
    parent_path = relative_path.rpartition("/")[0] or "."
    parent = _open_beneath(root_descriptor, parent_path, directory=True)
    try:
        actual = os.fstat(parent)
        expected = os.fstat(parent_descriptor)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("protected openat2 parent identity changed")
    finally:
        os.close(parent)
    try:
        opened = _open_beneath(root_descriptor, relative_path, directory=False)
    except FileNotFoundError:
        if object_descriptor is None:
            return
        raise
    try:
        if object_descriptor is None:
            raise ValueError("protected openat2 absent leaf appeared")
        actual = os.fstat(opened)
        expected = os.fstat(object_descriptor)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("protected openat2 object identity changed")
    finally:
        os.close(opened)
