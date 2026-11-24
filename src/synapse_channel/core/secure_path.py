# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — portable owner-only path identity and ACL floors
"""Portable user identity and owner-only path floors for POSIX and Windows.

Secret and private-directory loaders need one policy that works on every
platform CI exercises: Linux and macOS prove owner-only via POSIX mode bits
and effective uid; Windows proves the same intent via the process token SID
and a restrictive NT DACL inspected and applied through held handles. This
module is the single place those proofs live so callers never weaken security
to “read any file on non-POSIX”.

When the platform cannot prove the floor, helpers raise rather than pretend.
"""

from __future__ import annotations

import ctypes
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from synapse_channel.core.errors import SynapseError

_GROUP_OTHER_BITS: Final = 0o077
"""Permission bits that grant any non-owner access on POSIX."""

_POSIX: Final = os.name == "posix"
"""Whether this process runs on a POSIX host."""

_WINDOWS: Final = os.name == "nt"
"""Whether this process runs on Windows/NT."""

# Well-known SIDs that may appear on owner-only Windows secrets without
# meaning “world readable”. SYSTEM and Administrators already control the
# machine; OpenSSH's Windows private-key check uses the same allowance.
# OWNER RIGHTS (S-1-3-4) is a virtual SID that always means “the current NT
# owner of this object” and is not a third-party principal.
_WINDOWS_ALLOWED_EXTRA_SIDS: Final = frozenset(
    {
        "S-1-5-18",  # NT AUTHORITY\SYSTEM
        "S-1-5-32-544",  # BUILTIN\Administrators
        "S-1-3-4",  # OWNER RIGHTS (virtual; tracks the file owner)
        "S-1-3-0",  # CREATOR OWNER (template ACE; often left by inheritance)
    }
)

# ACCESS_MASK bits that count as “can read or mutate this secret/path”.
_WINDOWS_INTERESTING_ACCESS: Final = (
    0x80000000  # GENERIC_READ
    | 0x40000000  # GENERIC_WRITE
    | 0x20000000  # GENERIC_EXECUTE
    | 0x10000000  # GENERIC_ALL
    | 0x00120089  # FILE_GENERIC_READ
    | 0x00120116  # FILE_GENERIC_WRITE
    | 0x001200A0  # FILE_GENERIC_EXECUTE
    | 0x001F01FF  # FILE_ALL_ACCESS
    | 0x00000001  # FILE_READ_DATA / FILE_LIST_DIRECTORY
    | 0x00000002  # FILE_WRITE_DATA / FILE_ADD_FILE
    | 0x00000004  # FILE_APPEND_DATA
    | 0x00000020  # FILE_EXECUTE / FILE_TRAVERSE
    | 0x00010000  # DELETE
    | 0x00020000  # READ_CONTROL
    | 0x00040000  # WRITE_DAC
    | 0x00080000  # WRITE_OWNER
)

_ACCESS_ALLOWED_ACE_TYPE: Final = 0x0
"""ACCESS_ALLOWED_ACE AceType value."""

_ACCESS_DENIED_ACE_TYPE: Final = 0x1
"""ACCESS_DENIED_ACE AceType value."""

_SUPPORTED_ACE_TYPES: Final = frozenset({_ACCESS_ALLOWED_ACE_TYPE, _ACCESS_DENIED_ACE_TYPE})
"""The only DACL ACE types accepted; every other type fails closed."""


class SecurePathError(SynapseError, ValueError):
    """Raised when a path cannot be proven owner-only on this platform."""

    code = "secure_path"


@dataclass(frozen=True, slots=True)
class PortableUserKey:
    r"""Opaque current-user identity for path ownership comparisons.

    Attributes
    ----------
    kind :
        ``"posix_uid"`` or ``"windows_sid"``.
    value :
        Effective uid as a decimal string, or a Windows SID string.
    """

    kind: str
    value: str

    def path_suffix(self) -> str:
        """Return a filesystem-safe suffix for per-user temp directories."""
        if self.kind == "posix_uid":
            return self.value
        # SID strings contain hyphens; keep them, strip the leading ``S-``.
        cleaned = self.value[2:] if self.value.startswith("S-") else self.value
        return cleaned.replace("\\", "_")


@dataclass(frozen=True, slots=True)
class WindowsAce:
    """One access-control entry used by the pure Windows owner-only policy."""

    ace_type: int
    mask: int
    sid: str


def owner_only_floor_available() -> bool:
    """Return whether this platform can prove owner-only file and directory floors.

    POSIX needs ``O_NOFOLLOW`` and ``geteuid``. Windows uses the native NT
    security APIs via stdlib ``ctypes`` with no external tools. Other platforms
    refuse.
    """
    if _POSIX and hasattr(os, "O_NOFOLLOW") and hasattr(os, "geteuid"):
        return True
    if _WINDOWS:
        return True
    return False


def current_user_key() -> PortableUserKey:
    """Return the portable identity of the effective process user.

    Raises
    ------
    SecurePathError
        When the platform cannot resolve a stable user identity.
    """
    if _POSIX and hasattr(os, "geteuid"):
        return PortableUserKey(kind="posix_uid", value=str(os.geteuid()))
    if _POSIX and hasattr(os, "getuid"):
        return PortableUserKey(kind="posix_uid", value=str(os.getuid()))
    if _WINDOWS:
        from synapse_channel.core.secure_path_windows import windows_current_user_sid

        return PortableUserKey(kind="windows_sid", value=windows_current_user_sid())
    raise SecurePathError("portable user identity is unavailable on this platform")


def private_temp_user_segment() -> str:
    """Return the per-user segment for private temp roots (``synapse-user-…``)."""
    return current_user_key().path_suffix()


def apply_owner_only_file(path: str | Path) -> None:
    """Restrict ``path`` to owner-only access after create or rewrite.

    On POSIX applies ``chmod 0o600``. On Windows applies a protected owner and
    SYSTEM DACL through a held no-follow handle via ``SetSecurityInfo`` and
    verifies the descriptor back through that handle, failing closed on any
    error with no subprocess fallback.
    """
    target = Path(path)
    if _POSIX:
        os.chmod(target, 0o600, follow_symlinks=False)
        return
    if _WINDOWS:
        from synapse_channel.core.secure_path_windows import windows_apply_owner_only

        windows_apply_owner_only(target, directory=False)
        return
    raise SecurePathError(f"cannot apply owner-only mode on this platform: {target}")


def create_owner_only_file(path: str | Path) -> bool:
    """Create ``path`` empty and owner-only; return ``False`` when it already exists.

    On POSIX the file is created exclusively (``O_CREAT | O_EXCL``) with mode
    ``0o600`` and then restricted with :func:`apply_owner_only_file`. On Windows
    the file is created exclusively via ``CreateFileW`` with a birth-time
    protected owner/SYSTEM security descriptor so it never briefly inherits
    broad parent permissions. An existing path is left unchanged for the caller
    to prove owner-only.

    Parameters
    ----------
    path : str or Path
        The file to create inside an owner-only directory.

    Returns
    -------
    bool
        ``True`` when this call created the file, ``False`` when the path existed.
    """
    target = Path(path)
    if _WINDOWS:
        from synapse_channel.core.secure_path_windows import windows_create_owner_only_file

        return windows_create_owner_only_file(target)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    os.close(descriptor)
    apply_owner_only_file(target)
    return True


def apply_owner_only_dir(path: str | Path) -> None:
    """Restrict ``path`` to owner-only directory access (``0o700`` / NT DACL)."""
    target = Path(path)
    if _POSIX:
        os.chmod(target, 0o700, follow_symlinks=False)
        return
    if _WINDOWS:
        from synapse_channel.core.secure_path_windows import windows_apply_owner_only

        windows_apply_owner_only(target, directory=True)
        return
    raise SecurePathError(f"cannot apply owner-only directory mode on this platform: {target}")


def assert_posix_owner_only_file_info(
    info: os.stat_result,
    *,
    path: Path,
    purpose: str,
    require_single_link: bool = False,
) -> None:
    """Raise :class:`SecurePathError` unless ``info`` is an owner-only regular file.

    Used by same-descriptor POSIX loaders that already hold an ``fstat`` result.
    """
    if not stat.S_ISREG(info.st_mode):
        raise SecurePathError(f"{purpose}: {path} is not a regular file")
    if not hasattr(os, "geteuid") or info.st_uid != os.geteuid():
        raise SecurePathError(f"{purpose}: {path} is not owned by the effective user")
    if require_single_link and info.st_nlink != 1:
        raise SecurePathError(
            f"{purpose}: {path} has {info.st_nlink} hard links; an owner-only "
            "policy file must have exactly one link"
        )
    mode = stat.S_IMODE(info.st_mode)
    if mode & _GROUP_OTHER_BITS:
        raise SecurePathError(
            f"{purpose}: {path} is accessible by other users (mode {mode:03o}); "
            f"must be owner-only — run: chmod 600 {path}"
        )


def _assert_posix_owner_only_dir_info(
    info: os.stat_result,
    *,
    path: Path,
    purpose: str,
) -> None:
    """Raise :class:`SecurePathError` unless ``info`` is an owner-only directory."""
    if not stat.S_ISDIR(info.st_mode):
        raise SecurePathError(f"{purpose}: {path} is not a directory")
    if not hasattr(os, "geteuid") or info.st_uid != os.geteuid():
        raise SecurePathError(f"{purpose}: {path} is not owned by the effective user")
    mode = stat.S_IMODE(info.st_mode)
    if mode & _GROUP_OTHER_BITS:
        raise SecurePathError(
            f"{purpose}: {path} is accessible by other users (mode {mode:03o}); "
            f"must be owner-only — run: chmod 700 {path}"
        )


def assert_owner_only_descriptor(
    descriptor: int,
    *,
    path: str | Path,
    purpose: str,
    directory: bool = False,
    require_single_link: bool = False,
) -> None:
    """Prove an already-open file or directory descriptor is owner-only.

    On POSIX validates ``os.fstat(descriptor)`` directly. On Windows converts
    the descriptor to its held NT handle and proves non-reparse status, file or
    directory kind, hard-link count (when requested), and owner-only NT DACL
    via ``GetFileInformationByHandle`` and ``GetSecurityInfo`` on that handle.

    Parameters
    ----------
    descriptor : int
        Open file descriptor owned by the caller.
    path : str or Path
        Display path used in failure messages.
    purpose : str
        Short label naming the caller's security context.
    directory : bool, optional
        When ``True``, require a directory descriptor instead of a regular file.
    require_single_link : bool, optional
        When ``True`` on a regular file, refuse hard-link counts other than 1.

    Raises
    ------
    SecurePathError
        When the platform floor is unavailable or the descriptor fails any
        ownership, kind, link-count, or ACL check.
    """
    if not owner_only_floor_available():
        raise SecurePathError(
            f"{purpose}: secure owner-only file validation is unavailable on this platform"
        )
    target = Path(path)
    if _WINDOWS:
        from synapse_channel.core.secure_path_windows import windows_assert_owner_only_descriptor

        windows_assert_owner_only_descriptor(
            descriptor,
            path=target,
            purpose=purpose,
            directory=directory,
            require_single_link=require_single_link,
        )
        return
    try:
        info = os.fstat(descriptor)
    except OSError as exc:
        raise SecurePathError(f"{purpose}: cannot stat {target}: {exc.strerror or exc}") from exc
    if directory:
        _assert_posix_owner_only_dir_info(info, path=target, purpose=purpose)
        return
    assert_posix_owner_only_file_info(
        info,
        path=target,
        purpose=purpose,
        require_single_link=require_single_link,
    )


def assert_owner_only_file_path(
    path: str | Path,
    *,
    purpose: str,
    require_single_link: bool = False,
) -> None:
    """Prove ``path`` is a regular file only the current user can read.

    On POSIX uses ``lstat`` mode bits and effective uid. On Windows opens one
    no-follow NT handle and proves non-reparse status, regular-file kind,
    link count (when requested), owner SID, and DACL on that held handle.
    """
    if not owner_only_floor_available():
        raise SecurePathError(
            f"{purpose}: secure owner-only file validation is unavailable on this platform"
        )
    target = Path(path)
    if _WINDOWS:
        from synapse_channel.core.secure_path_windows import windows_assert_owner_only_path

        windows_assert_owner_only_path(
            target,
            purpose=purpose,
            directory=False,
            require_single_link=require_single_link,
        )
        return
    try:
        info = os.lstat(target)
    except OSError as exc:
        raise SecurePathError(f"{purpose}: cannot stat {target}: {exc.strerror or exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SecurePathError(f"{purpose}: {target} is a symlink; refused")
    assert_posix_owner_only_file_info(
        info,
        path=target,
        purpose=purpose,
        require_single_link=require_single_link,
    )


def assert_owner_only_dir_path(path: str | Path, *, purpose: str) -> None:
    """Prove ``path`` is a directory only the current user can access."""
    if not owner_only_floor_available():
        raise SecurePathError(
            f"{purpose}: owner-only directory validation is unavailable on this platform"
        )
    target = Path(path)
    if _WINDOWS:
        from synapse_channel.core.secure_path_windows import windows_assert_owner_only_path

        windows_assert_owner_only_path(target, purpose=purpose, directory=True)
        return
    try:
        info = os.lstat(target)
    except OSError as exc:
        raise SecurePathError(f"{purpose}: cannot stat {target}: {exc.strerror or exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SecurePathError(f"{purpose}: {target} is a symlink; refused")
    _assert_posix_owner_only_dir_info(info, path=target, purpose=purpose)


# kernel32, bound on Windows only. Tests replace a method on this object.
# Any: this tree's Linux mypy ctypes stub has no WinDLL.
_win_kernel: Any = None


def _windows_kernel() -> Any:
    """Return the process kernel32, loading it once on Windows."""
    global _win_kernel
    if sys.platform != "win32":
        raise OSError("the Win32 kernel is unavailable on this platform")
    loaded = _win_kernel
    if loaded is not None:
        return loaded
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    _win_kernel = kernel
    return kernel


def _windows_open_nofollow(target: Path, *, directory: bool) -> int:
    """Open ``target`` without following a final reparse point.

    One ``CreateFileW`` asks the kernel not to follow the leaf. Attributes are
    read from that handle. A reparse point is closed and refused. Any other
    handle becomes a descriptor the caller closes. ``directory`` does not add
    backup semantics, so the kernel rejects a directory handle.

    Parameters
    ----------
    target : pathlib.Path
        Leaf to open.
    directory : bool
        Accepted so callers keep one signature. A directory is still rejected.

    Returns
    -------
    int
        An open descriptor.

    Raises
    ------
    OSError
        The leaf is missing, is a reparse point, the kernel refuses it, or this
        process is not Windows.
    """
    if sys.platform != "win32":
        raise OSError("secure nofollow open is unavailable on this platform")
    import msvcrt

    kernel = _windows_kernel()
    # FILE_FLAG_OPEN_REPARSE_POINT: the handle is the leaf, not its target.
    # ``directory`` does not add backup semantics, so the kernel rejects a directory.
    flags = 0x00200000
    handle = kernel.CreateFileW(str(target), 0x80000000, 0x00000007, None, 3, flags, None)
    invalid = ctypes.c_void_p(-1).value
    if not handle or handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())

    class _FileTime(ctypes.Structure):
        _fields_ = (("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32))

    class _ByHandle(ctypes.Structure):
        _fields_ = (
            ("dwFileAttributes", ctypes.c_uint32),
            ("ftCreationTime", _FileTime),
            ("ftLastAccessTime", _FileTime),
            ("ftLastWriteTime", _FileTime),
            ("dwVolumeSerialNumber", ctypes.c_uint32),
            ("nFileSizeHigh", ctypes.c_uint32),
            ("nFileSizeLow", ctypes.c_uint32),
            ("nNumberOfLinks", ctypes.c_uint32),
            ("nFileIndexHigh", ctypes.c_uint32),
            ("nFileIndexLow", ctypes.c_uint32),
        )

    info = _ByHandle()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    if info.dwFileAttributes & 0x00000400:
        kernel.CloseHandle(handle)
        raise OSError(None, "symlink refused", str(target), 22)
    descriptor_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        return int(msvcrt.open_osfhandle(int(handle), descriptor_flags))
    except OSError:
        kernel.CloseHandle(handle)
        raise


def open_nofollow_leaf(path: str | Path, *, directory: bool = False) -> int:
    """Open the leaf of ``path`` without following a final link.

    On POSIX this is one ``O_NOFOLLOW`` open. On Windows it is one
    ``CreateFileW`` with ``FILE_FLAG_OPEN_REPARSE_POINT``. Attributes are read
    from that handle, a reparse point is refused, and the handle becomes a
    descriptor. Callers that need every parent component walked keep using
    :mod:`synapse_channel.core.secret_files`. A host that is neither POSIX nor
    Windows raises.

    Returns
    -------
    int
        An open file descriptor the caller must close.
    """
    target = Path(path)
    if sys.platform == "win32" and _WINDOWS:
        return _windows_open_nofollow(target, directory=directory)
    if _POSIX and hasattr(os, "O_NOFOLLOW"):
        flags = (
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        if directory:
            flags |= getattr(os, "O_DIRECTORY", 0)
        return os.open(target, flags)
    raise OSError("secure nofollow open is unavailable on this platform")


def _read_exact_file_bytes(descriptor: int, size: int, *, path: Path, purpose: str) -> bytes:
    """Refuse a short or oversized read from an already validated descriptor."""
    material = os.read(descriptor, size + 1)
    if len(material) != size:
        raise SecurePathError(
            f"{purpose}: {path} must hold exactly {size} bytes (found {len(material)})"
        )
    return material


def _read_bounded_file_bytes(
    descriptor: int,
    *,
    size: int,
    max_bytes: int | None,
    path: Path,
    purpose: str,
) -> bytes:
    """Read bounded bytes from an already validated descriptor."""
    limit = max_bytes if max_bytes is not None else size
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65_536, max(1, limit + 1 - total)))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise SecurePathError(
                f"{purpose}: {path} exceeds the {max_bytes}-byte secret-file limit"
            )
    return b"".join(chunks)


def read_owner_only_file_bytes(
    path: str | Path,
    *,
    purpose: str,
    expected_size: int | None = None,
    max_bytes: int | None = None,
    require_single_link: bool = False,
) -> bytes:
    """Read secret file bytes after an owner-only floor on the same held handle.

    POSIX opens with ``O_NOFOLLOW``, proves the floor on ``fstat`` of that
    descriptor, then reads from the same handle. Windows opens with
    :func:`open_nofollow_leaf` (refusing reparse points), proves the NT owner
    and DACL through :func:`assert_owner_only_descriptor` on that held handle,
    and reads from the same descriptor with no path-string TOCTOU window.

    Parameters
    ----------
    path :
        Secret file path (must be a regular file, not a symlink leaf).
    purpose :
        Short label for error messages (for example ``key file``).
    expected_size :
        When set, the file must hold exactly this many bytes.
    max_bytes :
        When set without ``expected_size``, refuse files larger than this.
        When neither size bound is set, the whole file is read (callers that
        need a hard cap must pass one).
    require_single_link :
        Refuse a policy inode with additional hard links, retaining the
        existing permissive default for ordinary key-byte callers.

    Returns
    -------
    bytes
        File contents.

    Raises
    ------
    SecurePathError
        Floor or kind failures (symlink, non-regular, non-owner, loose ACL).
    OSError
        Open/read failures not already mapped to :class:`SecurePathError`.
    """
    if not owner_only_floor_available():
        raise SecurePathError(
            f"{purpose}: secure owner-only file validation is unavailable on this platform"
        )
    target = Path(path)
    if expected_size is not None and expected_size < 0:
        raise SecurePathError(f"{purpose}: expected_size must be non-negative")
    if max_bytes is not None and max_bytes < 0:
        raise SecurePathError(f"{purpose}: max_bytes must be non-negative")
    try:
        fd = open_nofollow_leaf(target, directory=False)
    except FileNotFoundError as exc:
        raise SecurePathError(f"{purpose}: {target} does not exist") from exc
    except OSError as exc:
        detail = getattr(exc, "strerror", None) or str(exc)
        raise SecurePathError(f"{purpose}: cannot open {target}: {detail}") from exc
    try:
        assert_owner_only_descriptor(
            fd,
            path=target,
            purpose=purpose,
            directory=False,
            require_single_link=require_single_link,
        )
        size = int(os.fstat(fd).st_size)
        if expected_size is not None and size != expected_size:
            raise SecurePathError(
                f"{purpose}: {target} must hold exactly {expected_size} bytes (found {size})"
            )
        if max_bytes is not None and size > max_bytes:
            raise SecurePathError(
                f"{purpose}: {target} exceeds the {max_bytes}-byte secret-file limit"
            )
        if expected_size is not None:
            return _read_exact_file_bytes(fd, expected_size, path=target, purpose=purpose)
        return _read_bounded_file_bytes(
            fd, size=size, max_bytes=max_bytes, path=target, purpose=purpose
        )
    finally:
        os.close(fd)


def evaluate_windows_owner_only_policy(
    *,
    path: Path,
    purpose: str,
    owner_sid: str,
    current_sid: str,
    dacl_present: bool,
    aces: tuple[WindowsAce, ...],
) -> None:
    """Raise :class:`SecurePathError` unless the NT DACL is owner-only.

    Pure policy (no ctypes): unit-tested on every platform. The Windows native
    loader gathers SIDs/ACEs from a held handle and calls this function.

    Ownership: the NT owner SID must be the current user, or a well-known
    privileged SID (Administrators / SYSTEM / OWNER RIGHTS / CREATOR OWNER)
    that commonly owns files created under an elevated or admin-group token
    on Windows CI. A privileged owner with a DACL that grants interesting
    access only to the current user and other allowlisted SIDs is accepted;
    ambient admin rights alone are not enough if Everyone/Users-style ACEs
    remain. Fail closed on a NULL DACL, any ACE type other than standard
    allow (``0x0``) or deny (``0x1``), or any non-allowlisted allow principal.
    """
    allowed_owners = {current_sid} | _WINDOWS_ALLOWED_EXTRA_SIDS
    if owner_sid not in allowed_owners:
        raise SecurePathError(
            f"{purpose}: {path} is not owned by the effective user "
            f"(owner={owner_sid}, user={current_sid})"
        )
    if not dacl_present:
        raise SecurePathError(f"{purpose}: {path} has a NULL DACL (world-accessible); refused")
    for ace in aces:
        if ace.ace_type not in _SUPPORTED_ACE_TYPES:
            raise SecurePathError(
                f"{purpose}: {path} has unsupported ACE type {ace.ace_type}; refused"
            )
        if ace.ace_type == _ACCESS_DENIED_ACE_TYPE:
            continue
        if ace.sid == current_sid or ace.sid in _WINDOWS_ALLOWED_EXTRA_SIDS:
            continue
        if ace.mask & _WINDOWS_INTERESTING_ACCESS:
            raise SecurePathError(
                f"{purpose}: {path} is accessible by other principals "
                f"(ACE for {ace.sid}); must be owner-only"
            )
