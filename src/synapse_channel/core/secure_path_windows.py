# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Windows NT held-handle security descriptor custody
"""Inspect and apply NT owner-only security descriptors through held handles.

This sibling module owns every Win32 ``advapi32`` / ``kernel32`` security call
for :mod:`synapse_channel.core.secure_path`. Nothing in this module is exported
on :mod:`synapse_channel.federation_api`: raw handles, ``ctypes`` structures,
and birth-descriptor helpers remain internal to Core.
"""

from __future__ import annotations

import ctypes
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any, Final

from synapse_channel.core.secure_path import (
    SecurePathError,
    WindowsAce,
    evaluate_windows_owner_only_policy,
)

SE_FILE_OBJECT: Final = 1
OWNER_SECURITY_INFORMATION: Final = 0x00000001
DACL_SECURITY_INFORMATION: Final = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION: Final = 0x80000000
TOKEN_QUERY: Final = 0x0008
TOKEN_USER_INFO_CLASS: Final = 1
SDDL_REVISION_1: Final = 1

GENERIC_WRITE: Final = 0x40000000
READ_CONTROL: Final = 0x00020000
WRITE_DAC: Final = 0x00040000
WRITE_OWNER: Final = 0x00080000
FILE_READ_ATTRIBUTES: Final = 0x00000080
FILE_SHARE_ALL: Final = 0x00000007
CREATE_NEW: Final = 1
OPEN_EXISTING: Final = 3

FILE_ATTRIBUTE_DIRECTORY: Final = 0x00000010
FILE_ATTRIBUTE_NORMAL: Final = 0x00000080
FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x00000400
FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000

ERROR_FILE_NOT_FOUND: Final = 2
ERROR_PATH_NOT_FOUND: Final = 3
ERROR_FILE_EXISTS: Final = 80
ERROR_ALREADY_EXISTS: Final = 183
ERROR_INVALID_OWNER: Final = 1307

ACCESS_ALLOWED_ACE_TYPE: Final = 0x0
ACCESS_DENIED_ACE_TYPE: Final = 0x1
SUPPORTED_ACE_TYPES: Final = frozenset({ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE})


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
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
    ]


class _AclHeader(ctypes.Structure):
    _fields_ = [
        ("AclRevision", wintypes.BYTE),
        ("Sbz1", wintypes.BYTE),
        ("AclSize", wintypes.WORD),
        ("AceCount", wintypes.WORD),
        ("Sbz2", wintypes.WORD),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", wintypes.BYTE),
        ("AceFlags", wintypes.BYTE),
        ("AceSize", wintypes.WORD),
    ]


def _last_error() -> int:
    """Return the Win32 thread last-error code via ``ctypes.get_last_error``."""
    ctypes_mod: Any = ctypes
    return int(ctypes_mod.get_last_error())


def _invalid_handle_value() -> Any:
    """Return ``INVALID_HANDLE_VALUE`` without truncating pointer width."""
    return ctypes.c_void_p(-1).value


def _load_kernel32() -> Any:
    """Load a fresh ``kernel32`` handle with full prototypes and ``use_last_error``."""
    ctypes_mod: Any = ctypes
    kernel32 = ctypes_mod.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _bind_advapi32_descriptor_ops(advapi32: Any) -> None:
    """Bind descriptor and ACL prototypes onto a fresh ``advapi32`` handle."""
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorOwner.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.SetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetSecurityInfo.restype = wintypes.DWORD
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL


def _load_advapi32() -> Any:
    """Load a fresh ``advapi32`` handle with full prototypes and ``use_last_error``."""
    ctypes_mod: Any = ctypes
    advapi32 = ctypes_mod.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _bind_advapi32_descriptor_ops(advapi32)
    return advapi32


def _sid_to_string(advapi32: Any, kernel32: Any, sid_ptr: ctypes.c_void_p, *, label: str) -> str:
    """Convert one binary SID pointer into a string SID and free the Win32 buffer."""
    encoded = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(encoded)):
        raise SecurePathError(f"{label}: ConvertSidToStringSidW failed (error {_last_error()})")
    try:
        value = encoded.value
    finally:
        kernel32.LocalFree(encoded)
    if not value:
        raise SecurePathError(f"{label}: SID is empty")
    return value


def owner_birth_sddl(sid: str, *, directory: bool = False) -> str:
    """Return the owner/SYSTEM protected-DACL SDDL string for birth or apply.

    The format matches the fleet tier's birth descriptor verbatim so newly
    created private files and directories never briefly carry inherited grants.
    """
    if not sid or not sid.startswith("S-1-"):
        raise SecurePathError(f"invalid Windows owner SID for birth SDDL: {sid!r}")
    inheritance = "OICI" if directory else ""
    return f"O:{sid}D:P(A;{inheritance};FA;;;{sid})(A;{inheritance};FA;;;SY)"


@contextmanager
def owner_birth_security_descriptor(
    sid: str,
    *,
    directory: bool = False,
) -> Iterator[ctypes.c_void_p]:
    """Convert :func:`owner_birth_sddl` into a Win32 descriptor freed on exit."""
    sddl = owner_birth_sddl(sid, directory=directory)
    advapi32 = _load_advapi32()
    kernel32 = _load_kernel32()
    descriptor = ctypes.c_void_p()
    converted = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        SDDL_REVISION_1,
        ctypes.byref(descriptor),
        None,
    )
    if not converted or not descriptor.value:
        raise SecurePathError(
            f"ConvertStringSecurityDescriptorToSecurityDescriptorW failed (error {_last_error()})"
        )
    try:
        yield descriptor
    finally:
        kernel32.LocalFree(descriptor)


def windows_current_user_sid() -> str:
    """Read the current process token user SID natively; never falls back to a subprocess."""
    kernel32 = _load_kernel32()
    advapi32 = _load_advapi32()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise SecurePathError(
            f"cannot open process token via OpenProcessToken (error {_last_error()})"
        )
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, TOKEN_USER_INFO_CLASS, None, 0, ctypes.byref(size))
        if size.value == 0:
            raise SecurePathError(
                f"cannot size token user via GetTokenInformation (error {_last_error()})"
            )
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, TOKEN_USER_INFO_CLASS, buffer, size, ctypes.byref(size)
        ):
            raise SecurePathError(
                f"cannot read token user via GetTokenInformation (error {_last_error()})"
            )
        user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        return _sid_to_string(advapi32, kernel32, user.User.Sid, label="process token user SID")
    finally:
        kernel32.CloseHandle(token)


def _check_handle_file_info(
    kernel32: Any,
    handle: wintypes.HANDLE,
    *,
    path: Path,
    purpose: str,
    directory: bool,
    require_single_link: bool,
) -> None:
    """Verify non-reparse status, file/directory kind, and link count on a held handle."""
    info = _ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise SecurePathError(
            f"{purpose}: GetFileInformationByHandle failed for {path} (error {_last_error()})"
        )
    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise SecurePathError(f"{purpose}: {path} is a symlink; refused")
    is_dir = bool(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
    if directory and not is_dir:
        raise SecurePathError(f"{purpose}: {path} is not a directory")
    if not directory and is_dir:
        raise SecurePathError(f"{purpose}: {path} is not a regular file")
    if not directory and require_single_link and info.nNumberOfLinks != 1:
        raise SecurePathError(
            f"{purpose}: {path} has {info.nNumberOfLinks} hard links; an owner-only "
            "policy file must have exactly one link"
        )


def _decode_dacl_aces(
    advapi32: Any,
    kernel32: Any,
    dacl: ctypes.c_void_p,
    *,
    path: Path,
    purpose: str,
) -> tuple[WindowsAce, ...]:
    """Decode every ACE in ``dacl`` with bounds checks, failing closed on unknown types."""
    acl = ctypes.cast(dacl, ctypes.POINTER(_AclHeader)).contents
    if acl.AclSize < ctypes.sizeof(_AclHeader):
        raise SecurePathError(f"{purpose}: invalid DACL header for {path}")
    start = int(dacl.value or 0)
    end = start + int(acl.AclSize)
    collected: list[WindowsAce] = []
    for index in range(int(acl.AceCount)):
        ace_ptr = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, index, ctypes.byref(ace_ptr)):
            raise SecurePathError(f"{purpose}: GetAce failed for {path} (error {_last_error()})")
        if ace_ptr.value is None or not (start <= int(ace_ptr.value) <= end - 8):
            raise SecurePathError(f"{purpose}: invalid DACL ACE pointer for {path}")
        address = int(ace_ptr.value)
        header = _AceHeader.from_address(address)
        if header.AceSize < 16 or address + header.AceSize > end:
            raise SecurePathError(f"{purpose}: invalid DACL ACE size for {path}")
        if header.AceType not in SUPPORTED_ACE_TYPES:
            raise SecurePathError(
                f"{purpose}: {path} has unsupported ACE type {header.AceType}; refused"
            )
        mask = ctypes.c_uint32.from_address(address + 4).value
        ace_sid = _sid_to_string(
            advapi32,
            kernel32,
            ctypes.c_void_p(address + 8),
            label=f"{purpose}: cannot convert ACE SID for {path}",
        )
        collected.append(WindowsAce(ace_type=int(header.AceType), mask=int(mask), sid=ace_sid))
    return tuple(collected)


def windows_read_owner_and_aces_handle(
    handle: wintypes.HANDLE,
    *,
    path: Path,
    purpose: str,
) -> tuple[str, bool, tuple[WindowsAce, ...]]:
    """Return ``(owner_sid, dacl_present, aces)`` for a held NT handle via ``GetSecurityInfo``."""
    advapi32 = _load_advapi32()
    kernel32 = _load_kernel32()
    owner_sid = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = int(
        advapi32.GetSecurityInfo(
            handle,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            ctypes.byref(owner_sid),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
    )
    if status != 0:
        raise SecurePathError(
            f"{purpose}: cannot read security descriptor for {path} via GetSecurityInfo "
            f"(error {status})"
        )
    try:
        if not owner_sid.value:
            raise SecurePathError(f"{purpose}: missing owner SID for {path}")
        owner_value = _sid_to_string(
            advapi32,
            kernel32,
            owner_sid,
            label=f"{purpose}: cannot convert owner SID for {path}",
        )
        if not dacl or not dacl.value:
            return owner_value, False, ()
        aces = _decode_dacl_aces(advapi32, kernel32, dacl, path=path, purpose=purpose)
        return owner_value, True, aces
    finally:
        if descriptor.value:
            kernel32.LocalFree(descriptor)


def _open_nofollow_handle(
    kernel32: Any,
    target: Path,
    *,
    desired_access: int,
    directory: bool,
    purpose: str,
) -> wintypes.HANDLE:
    """Open an existing file or directory leaf without following a final reparse point."""
    flags = FILE_FLAG_OPEN_REPARSE_POINT | (FILE_FLAG_BACKUP_SEMANTICS if directory else 0)
    handle = kernel32.CreateFileW(
        str(target),
        desired_access,
        FILE_SHARE_ALL,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if not handle or handle == _invalid_handle_value():
        err = _last_error()
        if err in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
            raise SecurePathError(f"{purpose}: {target} does not exist")
        raise SecurePathError(f"{purpose}: cannot open {target} via CreateFileW (error {err})")
    return wintypes.HANDLE(handle)


def windows_assert_owner_only_handle(
    handle: wintypes.HANDLE,
    *,
    path: Path,
    purpose: str,
    directory: bool,
    require_single_link: bool,
    current_sid: str | None = None,
) -> None:
    """Prove non-reparse kind, link count, and owner-only NT DACL on one held handle."""
    kernel32 = _load_kernel32()
    _check_handle_file_info(
        kernel32,
        handle,
        path=path,
        purpose=purpose,
        directory=directory,
        require_single_link=require_single_link,
    )
    owner_value, dacl_present, aces = windows_read_owner_and_aces_handle(
        handle, path=path, purpose=purpose
    )
    sid = current_sid if current_sid is not None else windows_current_user_sid()
    evaluate_windows_owner_only_policy(
        path=path,
        purpose=purpose,
        owner_sid=owner_value,
        current_sid=sid,
        dacl_present=dacl_present,
        aces=aces,
    )


def windows_assert_owner_only_descriptor(
    descriptor: int,
    *,
    path: Path,
    purpose: str,
    directory: bool = False,
    require_single_link: bool = False,
) -> None:
    """Prove NT owner-only custody on the held handle backing a CRT descriptor."""
    import msvcrt

    msvcrt_mod: Any = msvcrt
    try:
        raw_handle = int(msvcrt_mod.get_osfhandle(descriptor))
    except OSError as exc:
        raise SecurePathError(
            f"{purpose}: cannot resolve Windows handle for {path}: {exc.strerror or exc}"
        ) from exc
    if not raw_handle or raw_handle == _invalid_handle_value():
        raise SecurePathError(f"{purpose}: invalid Windows handle for {path}")
    windows_assert_owner_only_handle(
        wintypes.HANDLE(raw_handle),
        path=path,
        purpose=purpose,
        directory=directory,
        require_single_link=require_single_link,
    )


def windows_assert_owner_only_path(
    path: Path,
    *,
    purpose: str,
    directory: bool,
    require_single_link: bool = False,
) -> None:
    """Open ``path`` no-follow once and prove owner-only NT custody on that held handle."""
    kernel32 = _load_kernel32()
    handle = _open_nofollow_handle(
        kernel32,
        path,
        desired_access=READ_CONTROL | FILE_READ_ATTRIBUTES,
        directory=directory,
        purpose=purpose,
    )
    try:
        windows_assert_owner_only_handle(
            handle,
            path=path,
            purpose=purpose,
            directory=directory,
            require_single_link=require_single_link,
        )
    finally:
        kernel32.CloseHandle(handle)


def windows_create_owner_only_file(target: Path) -> bool:
    """Create ``target`` exclusively with a birth-time protected owner/SYSTEM DACL."""
    sid = windows_current_user_sid()
    kernel32 = _load_kernel32()
    with owner_birth_security_descriptor(sid, directory=False) as sd:
        attrs = _SecurityAttributes(
            nLength=ctypes.sizeof(_SecurityAttributes),
            lpSecurityDescriptor=sd,
            bInheritHandle=False,
        )
        handle = kernel32.CreateFileW(
            str(target),
            GENERIC_WRITE,
            0,
            ctypes.byref(attrs),
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if not handle or handle == _invalid_handle_value():
            err = _last_error()
            if err in (ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS):
                return False
            raise SecurePathError(
                f"cannot create owner-only file {target} via CreateFileW (error {err})"
            )
    try:
        return True
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def _extract_owner_and_dacl(
    advapi32: Any,
    descriptor: ctypes.c_void_p,
    *,
    target: Path,
) -> tuple[ctypes.c_void_p, ctypes.c_void_p]:
    """Read the owner SID and DACL pointers from a converted birth descriptor."""
    owner_ptr = ctypes.c_void_p()
    owner_defaulted = wintypes.BOOL()
    if (
        not advapi32.GetSecurityDescriptorOwner(
            descriptor, ctypes.byref(owner_ptr), ctypes.byref(owner_defaulted)
        )
        or not owner_ptr.value
    ):
        raise SecurePathError(
            f"cannot read owner from birth descriptor for {target} via "
            f"GetSecurityDescriptorOwner (error {_last_error()})"
        )
    dacl_present = wintypes.BOOL()
    dacl_ptr = ctypes.c_void_p()
    dacl_defaulted = wintypes.BOOL()
    if (
        not advapi32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl_ptr),
            ctypes.byref(dacl_defaulted),
        )
        or not dacl_present.value
        or not dacl_ptr.value
    ):
        raise SecurePathError(
            f"cannot read DACL from birth descriptor for {target} via "
            f"GetSecurityDescriptorDacl (error {_last_error()})"
        )
    return owner_ptr, dacl_ptr


def windows_apply_owner_only(target: Path, *, directory: bool) -> None:
    """Apply a protected owner/SYSTEM DACL via ``SetSecurityInfo`` and verify the same handle."""
    purpose = "owner-only directory" if directory else "owner-only file"
    sid = windows_current_user_sid()
    kernel32 = _load_kernel32()
    advapi32 = _load_advapi32()
    handle = _open_nofollow_handle(
        kernel32,
        target,
        desired_access=WRITE_DAC | WRITE_OWNER | READ_CONTROL | FILE_READ_ATTRIBUTES,
        directory=directory,
        purpose=purpose,
    )
    try:
        _check_handle_file_info(
            kernel32,
            handle,
            path=target,
            purpose=purpose,
            directory=directory,
            require_single_link=False,
        )
        with owner_birth_security_descriptor(sid, directory=directory) as sd:
            owner_ptr, dacl_ptr = _extract_owner_and_dacl(advapi32, sd, target=target)
            status = int(
                advapi32.SetSecurityInfo(
                    handle,
                    SE_FILE_OBJECT,
                    OWNER_SECURITY_INFORMATION
                    | DACL_SECURITY_INFORMATION
                    | PROTECTED_DACL_SECURITY_INFORMATION,
                    owner_ptr,
                    None,
                    dacl_ptr,
                    None,
                )
            )
        if status == ERROR_INVALID_OWNER:
            raise SecurePathError(
                f"cannot set owner-only security on {target} via SetSecurityInfo: "
                f"ERROR_INVALID_OWNER (error {status})"
            )
        if status != 0:
            raise SecurePathError(
                f"cannot apply owner-only ACL on {target} via SetSecurityInfo (error {status})"
            )
        owner_value, dacl_present, aces = windows_read_owner_and_aces_handle(
            handle, path=target, purpose=purpose
        )
        evaluate_windows_owner_only_policy(
            path=target,
            purpose=purpose,
            owner_sid=owner_value,
            current_sid=sid,
            dacl_present=dacl_present,
            aces=aces,
        )
    finally:
        kernel32.CloseHandle(handle)
