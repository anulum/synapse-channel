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
and a restrictive DACL. This module is the single place those proofs live so
callers never weaken security to “read any file on non-POSIX”.

When the platform cannot prove the floor, helpers raise rather than pretend.
"""

from __future__ import annotations

import os
import stat
import subprocess  # nosec B404 - fixed icacls/whoami argv only; never a shell string
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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
_WINDOWS_ALLOWED_EXTRA_SIDS: Final = frozenset(
    {
        "S-1-5-18",  # NT AUTHORITY\SYSTEM
        "S-1-5-32-544",  # BUILTIN\Administrators
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


def owner_only_floor_available() -> bool:
    """Return whether this platform can prove owner-only file and directory floors.

    POSIX needs ``O_NOFOLLOW`` and ``geteuid``. Windows needs the NT security
    APIs (stdlib ``ctypes`` + ``icacls`` for apply). Other platforms refuse.
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
        return PortableUserKey(kind="windows_sid", value=_windows_current_user_sid())
    raise SecurePathError("portable user identity is unavailable on this platform")


def private_temp_user_segment() -> str:
    """Return the per-user segment for private temp roots (``synapse-user-…``)."""
    return current_user_key().path_suffix()


def apply_owner_only_file(path: str | Path) -> None:
    """Restrict ``path`` to owner-only access after create or rewrite.

    On POSIX applies ``chmod 0o600``. On Windows removes inheritance and grants
    full control only to the current user SID (fail closed on tool errors).
    """
    target = Path(path)
    if _POSIX:
        os.chmod(target, 0o600, follow_symlinks=False)
        return
    if _WINDOWS:
        _windows_apply_owner_only(target, directory=False)
        return
    raise SecurePathError(f"cannot apply owner-only mode on this platform: {target}")


def apply_owner_only_dir(path: str | Path) -> None:
    """Restrict ``path`` to owner-only directory access (``0o700`` / NT DACL)."""
    target = Path(path)
    if _POSIX:
        os.chmod(target, 0o700, follow_symlinks=False)
        return
    if _WINDOWS:
        _windows_apply_owner_only(target, directory=True)
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


def assert_owner_only_file_path(
    path: str | Path,
    *,
    purpose: str,
    require_single_link: bool = False,
) -> None:
    """Prove ``path`` is a regular file only the current user can read.

    On POSIX uses lstat mode bits and effective uid. On Windows uses the file
    owner SID and DACL (fail closed if the ACL cannot be read).
    """
    if not owner_only_floor_available():
        raise SecurePathError(
            f"{purpose}: secure owner-only file validation is unavailable on this platform"
        )
    target = Path(path)
    if _WINDOWS:
        _windows_assert_owner_only_path(target, purpose=purpose, directory=False)
        if require_single_link:
            # NTFS hard-link count is available via st_nlink on Windows Python.
            nlink = target.stat().st_nlink
            if nlink != 1:
                raise SecurePathError(
                    f"{purpose}: {target} has {nlink} hard links; an owner-only "
                    "policy file must have exactly one link"
                )
        return
    # POSIX path: lstat so a symlink leaf is not followed for mode checks.
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
        _windows_assert_owner_only_path(target, purpose=purpose, directory=True)
        return
    try:
        info = os.lstat(target)
    except OSError as exc:
        raise SecurePathError(f"{purpose}: cannot stat {target}: {exc.strerror or exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SecurePathError(f"{purpose}: {target} is a symlink; refused")
    if not stat.S_ISDIR(info.st_mode):
        raise SecurePathError(f"{purpose}: {target} is not a directory")
    if not hasattr(os, "geteuid") or info.st_uid != os.geteuid():
        raise SecurePathError(f"{purpose}: {target} is not owned by the effective user")
    mode = stat.S_IMODE(info.st_mode)
    if mode & _GROUP_OTHER_BITS:
        raise SecurePathError(
            f"{purpose}: {target} is accessible by other users (mode {mode:03o}); "
            f"must be owner-only — run: chmod 700 {target}"
        )


def open_nofollow_leaf(path: str | Path, *, directory: bool = False) -> int:
    """Open the leaf of ``path`` without following a final symlink when possible.

    On POSIX this is a simple ``O_NOFOLLOW`` open of the full path (callers that
    need full component walking keep using :mod:`secret_files`). On Windows the
    leaf is refused when it is a reparse point/symlink, then opened normally.

    Returns
    -------
    int
        An open file descriptor the caller must close.
    """
    target = Path(path)
    if _POSIX and hasattr(os, "O_NOFOLLOW"):
        flags = (
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        if directory:
            flags |= getattr(os, "O_DIRECTORY", 0)
        return os.open(target, flags)
    if _WINDOWS:
        if target.is_symlink():
            raise OSError(None, "symlink refused", str(target), 22)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        if directory:
            # Windows has no O_DIRECTORY; open and validate with fstat/isdir.
            pass
        return os.open(target, flags)
    raise OSError("secure nofollow open is unavailable on this platform")


# ---------------------------------------------------------------------------
# Windows SID / DACL helpers
# ---------------------------------------------------------------------------

_ACCESS_ALLOWED_ACE_TYPE: Final = 0x0
"""ACCESS_ALLOWED_ACE AceType value."""


@dataclass(frozen=True, slots=True)
class WindowsAce:
    """One access-control entry used by the pure Windows owner-only policy."""

    ace_type: int
    mask: int
    sid: str


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
    loader gathers SIDs/ACEs and calls this function.
    """
    if owner_sid != current_sid:
        raise SecurePathError(
            f"{purpose}: {path} is not owned by the effective user "
            f"(owner={owner_sid}, user={current_sid})"
        )
    if not dacl_present:
        raise SecurePathError(f"{purpose}: {path} has a NULL DACL (world-accessible); refused")
    for ace in aces:
        if ace.ace_type != _ACCESS_ALLOWED_ACE_TYPE:
            continue
        if ace.sid == current_sid or ace.sid in _WINDOWS_ALLOWED_EXTRA_SIDS:
            continue
        if ace.mask & _WINDOWS_INTERESTING_ACCESS:
            raise SecurePathError(
                f"{purpose}: {path} is accessible by other principals "
                f"(ACE for {ace.sid}); must be owner-only"
            )


def _windows_path_kind_guards(path: Path, *, purpose: str, directory: bool) -> None:
    """Refuse missing paths, symlinks, and wrong file kinds before ACL work."""
    if not path.exists():
        raise SecurePathError(f"{purpose}: {path} does not exist")
    if path.is_symlink():
        raise SecurePathError(f"{purpose}: {path} is a symlink; refused")
    is_dir = path.is_dir()
    if directory and not is_dir:
        raise SecurePathError(f"{purpose}: {path} is not a directory")
    if not directory and is_dir:
        raise SecurePathError(f"{purpose}: {path} is not a regular file")
    if not directory and not path.is_file():
        raise SecurePathError(f"{purpose}: {path} is not a regular file")


def _windows_current_user_sid() -> str:
    """Return the current process token user SID as a string.

    Prefer a properly prototyped ``OpenProcessToken`` path; fall back to the
    fixed ``whoami /user`` command (present on every supported Windows host)
    when the native path is unavailable.
    """
    try:
        return _windows_current_user_sid_native()
    except SecurePathError:
        return _windows_current_user_sid_whoami()


def _windows_current_user_sid_whoami() -> str:  # pragma: no cover - platform-native Win32
    """Resolve the current user SID via fixed ``whoami /user`` argv (no shell)."""
    try:
        # ``/fo`` is whoami's format switch (not a misspelling of "of"); split
        # so the spell-checker does not rewrite a fixed Windows argv token.
        whoami_format = "/" + "fo"
        result = subprocess.run(  # nosec B603 B607 - fixed whoami argv, never a shell string
            ["whoami", "/user", whoami_format, "csv", "/nh"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SecurePathError("whoami is required to resolve the Windows user SID") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SecurePathError(
            f"cannot resolve Windows user SID via whoami: {detail or result.returncode}"
        )
    # CSV row: "DOMAIN\\user","S-1-5-21-…"
    line = (result.stdout or "").strip().splitlines()
    if not line:
        raise SecurePathError("whoami returned no user SID")
    parts = [part.strip().strip('"') for part in line[0].split(",")]
    for part in reversed(parts):
        if part.startswith("S-1-"):
            return part
    raise SecurePathError(f"whoami output had no SID: {line[0]!r}")


def _windows_current_user_sid_native() -> str:  # pragma: no cover - platform-native Win32
    """Read the process token user SID via ``advapi32`` with full prototypes."""
    import ctypes
    from ctypes import wintypes
    from typing import Any

    token_user = 1
    token_query = 0x0008
    # Route through Any so Linux mypy (ctypes without WinDLL) stays clean.
    ctypes_mod: Any = ctypes
    # Fresh WinDLL handles with use_last_error so prototypes are not shared
    # pollution from other callers on the process-global windll caches.
    kernel32 = ctypes_mod.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes_mod.WinDLL("advapi32", use_last_error=True)

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]

    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE

    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    open_process_token.restype = wintypes.BOOL

    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_token_information.restype = wintypes.BOOL

    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    convert_sid.restype = wintypes.BOOL

    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not open_process_token(get_current_process(), token_query, ctypes.byref(token)):
        raise SecurePathError(
            f"cannot open process token for user identity: {ctypes_mod.get_last_error()}"
        )
    try:
        size = wintypes.DWORD(0)
        get_token_information(token, token_user, None, 0, ctypes.byref(size))
        if size.value == 0:
            raise SecurePathError("cannot size token user information")
        buffer = ctypes.create_string_buffer(size.value)
        if not get_token_information(token, token_user, buffer, size, ctypes.byref(size)):
            raise SecurePathError(
                f"cannot read token user information: {ctypes_mod.get_last_error()}"
            )
        user = ctypes.cast(buffer, ctypes.POINTER(TOKEN_USER)).contents
        sid_ptr = wintypes.LPWSTR()
        if not convert_sid(user.User.Sid, ctypes.byref(sid_ptr)):
            raise SecurePathError(
                f"cannot convert user SID to string: {ctypes_mod.get_last_error()}"
            )
        try:
            value = sid_ptr.value
        finally:
            local_free(sid_ptr)
        if not value:
            raise SecurePathError("process token user SID is empty")
        return value
    finally:
        close_handle(token)


def _windows_apply_owner_only(path: Path, *, directory: bool) -> None:
    """Apply an owner-only DACL via ``icacls`` (present on every supported Windows)."""
    sid = _windows_current_user_sid()
    rights = "(OI)(CI)(F)" if directory else "(F)"
    grant = f"*{sid}:{rights}"
    try:
        # Fixed system tool + path we own; never a shell string or untrusted binary.
        strip = subprocess.run(  # nosec B603 B607
            ["icacls", str(path), "/inheritance:r"],
            check=False,
            capture_output=True,
            text=True,
        )
        if strip.returncode != 0:
            detail = (strip.stderr or strip.stdout or "").strip()
            raise SecurePathError(
                f"cannot strip inherited ACL on {path}: {detail or strip.returncode}"
            )
        grant_run = subprocess.run(  # nosec B603 B607
            ["icacls", str(path), "/grant:r", grant],
            check=False,
            capture_output=True,
            text=True,
        )
        if grant_run.returncode != 0:
            detail = (grant_run.stderr or grant_run.stdout or "").strip()
            raise SecurePathError(
                f"cannot grant owner-only ACL on {path}: {detail or grant_run.returncode}"
            )
    except FileNotFoundError as exc:
        raise SecurePathError(
            f"icacls is required to apply owner-only ACLs on Windows ({path})"
        ) from exc


def _windows_read_owner_and_aces(
    path: Path, *, purpose: str
) -> tuple[str, bool, tuple[WindowsAce, ...]]:  # pragma: no cover - platform-native Win32
    """Return ``(owner_sid, dacl_present, aces)`` via ``GetNamedSecurityInfoW``."""
    import ctypes
    from ctypes import wintypes
    from typing import Any

    owner_security = 0x00000001
    dacl_security = 0x00000004
    se_file_object = 1

    ctypes_mod: Any = ctypes
    advapi32 = ctypes_mod.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes_mod.WinDLL("kernel32", use_last_error=True)

    get_named = advapi32.GetNamedSecurityInfoW
    get_named.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_named.restype = wintypes.DWORD

    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    convert_sid.restype = wintypes.BOOL

    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    owner_sid = ctypes.c_void_p()
    group_sid = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    sacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = get_named(
        str(path),
        se_file_object,
        owner_security | dacl_security,
        ctypes.byref(owner_sid),
        ctypes.byref(group_sid),
        ctypes.byref(dacl),
        ctypes.byref(sacl),
        ctypes.byref(descriptor),
    )
    if status != 0:
        raise SecurePathError(
            f"{purpose}: cannot read security descriptor for {path} (error {status})"
        )
    try:
        owner_string = wintypes.LPWSTR()
        if not convert_sid(owner_sid, ctypes.byref(owner_string)):
            raise SecurePathError(f"{purpose}: cannot convert owner SID for {path}")
        try:
            owner_value = owner_string.value or ""
        finally:
            local_free(owner_string)
        if not dacl or not dacl.value:
            return owner_value, False, ()

        class ACL(ctypes.Structure):
            _fields_ = [
                ("AclRevision", wintypes.BYTE),
                ("Sbz1", wintypes.BYTE),
                ("AclSize", wintypes.WORD),
                ("AceCount", wintypes.WORD),
                ("Sbz2", wintypes.WORD),
            ]

        class ACE_HEADER(ctypes.Structure):
            _fields_ = [
                ("AceType", wintypes.BYTE),
                ("AceFlags", wintypes.BYTE),
                ("AceSize", wintypes.WORD),
            ]

        acl = ctypes.cast(dacl, ctypes.POINTER(ACL)).contents
        ace_addr = int(dacl.value) + ctypes.sizeof(ACL)
        collected: list[WindowsAce] = []
        for _ in range(acl.AceCount):
            header = ACE_HEADER.from_address(ace_addr)
            if header.AceType == _ACCESS_ALLOWED_ACE_TYPE:
                mask = ctypes.c_uint32.from_address(ace_addr + ctypes.sizeof(ACE_HEADER)).value
                sid_addr = ace_addr + ctypes.sizeof(ACE_HEADER) + ctypes.sizeof(ctypes.c_uint32)
                sid_string = wintypes.LPWSTR()
                if not convert_sid(ctypes.c_void_p(sid_addr), ctypes.byref(sid_string)):
                    raise SecurePathError(f"{purpose}: cannot convert ACE SID for {path}")
                try:
                    ace_sid = sid_string.value or ""
                finally:
                    local_free(sid_string)
                collected.append(WindowsAce(ace_type=header.AceType, mask=mask, sid=ace_sid))
            ace_addr += header.AceSize
        return owner_value, True, tuple(collected)
    finally:
        if descriptor:
            local_free(descriptor)


def _windows_assert_owner_only_path(
    path: Path,
    *,
    purpose: str,
    directory: bool,
) -> None:
    """Fail closed unless ``path`` is owner-only under the NT security model."""
    _windows_path_kind_guards(path, purpose=purpose, directory=directory)
    owner_value, dacl_present, aces = _windows_read_owner_and_aces(path, purpose=purpose)
    evaluate_windows_owner_only_policy(
        path=path,
        purpose=purpose,
        owner_sid=owner_value,
        current_sid=_windows_current_user_sid(),
        dacl_present=dacl_present,
        aces=aces,
    )
