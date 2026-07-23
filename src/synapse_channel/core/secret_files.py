# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owner-only secret file loading for CLI flags
"""Load CLI secrets from owner-only files instead of argv.

A secret passed as ``--token SECRET`` is visible to every local user in the
process list; a file the CLI reads at startup is not. This module is the single
loader behind the ``*-file`` companions of secret-bearing hub flags
(``--metrics-token-file``, ``--message-auth-key-file``): it reads the file,
refuses one that other users could read, and never places file content in an
error message, so a wrongly permissioned or malformed secret can be reported and
logged without leaking what it protects.

Secure file forms are fail-closed owner-only surfaces. On POSIX the loader walks
every path component through directory descriptors with ``O_NOFOLLOW``, validates
the final descriptor as a regular file owned by the effective service user with
mode ``0600`` or stricter, reads only from it, then rechecks identity and
metadata. On Windows the loader opens the leaf without following a symlink and
proves owner-only access via the NT DACL (see
:mod:`~synapse_channel.core.secure_path`). Callers loading executable policy can
additionally require a single-link inode. Platforms that cannot prove those
invariants must use a validated native secret provider instead of these flags.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure_path import (
    SecurePathError,
    assert_owner_only_file_path,
    owner_only_floor_available,
)

_GROUP_OTHER_BITS = 0o077
"""Permission bits that grant any non-owner access to a secret file."""

DEFAULT_SECRET_FILE_LIMIT = 65_536
"""Maximum bytes accepted from one CLI secret file."""

_POSIX = os.name == "posix"
"""Whether this platform expresses POSIX permission modes; captured once at import."""

_WINDOWS = os.name == "nt"
"""Whether this platform uses NT security descriptors for the owner-only floor."""


class SecretFileError(SynapseError, ValueError):
    """Raised when a secret file is missing, malformed, or readable by others.

    The message names the flag and the path, never the file's content.
    """

    code = "secret_file"


def open_nofollow_descriptor(file_path: str | Path, *, directory: bool = False) -> int:
    """Open a path through no-follow directory descriptors for every component.

    The returned descriptor owns the exact final object. Callers must close it.
    A relative path starts at a descriptor for the current directory; an
    absolute path starts at the filesystem root. No component lookup is later
    repeated through an untrusted pathname.

    On Windows the full component walk is not available; the leaf is opened after
    refusing a symlink final path (see :func:`secure_path.open_nofollow_leaf`).
    """
    path = Path(file_path)
    if _WINDOWS:
        from synapse_channel.core.secure_path import open_nofollow_leaf

        return open_nofollow_leaf(path, directory=directory)
    if not _POSIX or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("secure nofollow path walking is unavailable on this platform")
    directory_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path.anchor or ".", directory_flags)
    components = path.parts[1:] if path.is_absolute() else path.parts
    try:
        for index, component in enumerate(components):
            final = index == len(components) - 1
            flags = (
                os.O_RDONLY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            if not final or directory:
                flags |= getattr(os, "O_DIRECTORY", 0)
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read_owner_only_text(
    path: Path,
    *,
    flag: str,
    limit: int = DEFAULT_SECRET_FILE_LIMIT,
    require_single_link: bool = False,
) -> str:
    """Read bounded UTF-8 from one same-descriptor owner-only regular file."""
    if not owner_only_floor_available():
        raise SecretFileError(
            f"{flag}: secure owner-only file validation is unavailable on this platform"
        )
    if _WINDOWS:
        return _read_owner_only_text_windows(
            path,
            flag=flag,
            limit=limit,
            require_single_link=require_single_link,
        )
    try:
        descriptor = open_nofollow_descriptor(path)
    except OSError as exc:
        raise SecretFileError(
            f"{flag}: cannot securely open {path}: {exc.strerror or exc}"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SecretFileError(f"{flag}: {path} is not a regular secret file")
        if info.st_uid != os.geteuid():
            raise SecretFileError(f"{flag}: {path} is not owned by the effective hub service user")
        if require_single_link and info.st_nlink != 1:
            raise SecretFileError(
                f"{flag}: {path} has {info.st_nlink} hard links; an owner-only policy file "
                "must have exactly one link"
            )
        mode = stat.S_IMODE(info.st_mode)
        if mode & _GROUP_OTHER_BITS:
            raise SecretFileError(
                f"{flag}: {path} is accessible by other users (mode {mode:03o}); a secret "
                f"file must be owner-only — run: chmod 600 {path}"
            )
        if info.st_size > limit:
            raise SecretFileError(f"{flag}: {path} exceeds the {limit}-byte secret-file limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise SecretFileError(f"{flag}: {path} exceeds the {limit}-byte secret-file limit")
        after = os.fstat(descriptor)
        before_identity = (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            stat.S_IMODE(info.st_mode),
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_uid,
            stat.S_IMODE(after.st_mode),
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity:
            raise SecretFileError(f"{flag}: {path} changed while its policy was being read")
    except OSError as exc:
        raise SecretFileError(
            f"{flag}: cannot securely read {path}: {exc.strerror or exc}"
        ) from exc
    finally:
        os.close(descriptor)
    try:
        return b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SecretFileError(f"{flag}: {path} is not valid UTF-8") from exc


def _read_owner_only_text_windows(
    path: Path,
    *,
    flag: str,
    limit: int,
    require_single_link: bool,
) -> str:
    """Windows branch: prove NT owner-only DACL, then read bounded UTF-8."""
    try:
        assert_owner_only_file_path(
            path,
            purpose=flag,
            require_single_link=require_single_link,
        )
    except SecurePathError as exc:
        # Map portable errors onto SecretFileError so CLI callers see one type.
        message = str(exc)
        if "effective user" in message or "not owned" in message:
            raise SecretFileError(
                f"{flag}: {path} is not owned by the effective hub service user"
            ) from exc
        if "accessible by other" in message or "NULL DACL" in message or "ACE for" in message:
            raise SecretFileError(
                f"{flag}: {path} is accessible by other users; a secret file must be "
                "owner-only (restrict the NT DACL to the current user)"
            ) from exc
        if "hard links" in message:
            raise SecretFileError(message) from exc
        if "symlink" in message:
            raise SecretFileError(f"{flag}: cannot securely open {path}: symlink refused") from exc
        if "not a regular" in message:
            raise SecretFileError(f"{flag}: {path} is not a regular secret file") from exc
        if "unavailable" in message:
            raise SecretFileError(
                f"{flag}: secure owner-only file validation is unavailable on this platform"
            ) from exc
        raise SecretFileError(f"{flag}: {message}") from exc
    try:
        descriptor = open_nofollow_descriptor(path)
    except OSError as exc:
        raise SecretFileError(
            f"{flag}: cannot securely open {path}: {exc.strerror or exc}"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SecretFileError(f"{flag}: {path} is not a regular secret file")
        if info.st_size > limit:
            raise SecretFileError(f"{flag}: {path} exceeds the {limit}-byte secret-file limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise SecretFileError(f"{flag}: {path} exceeds the {limit}-byte secret-file limit")
        after = os.fstat(descriptor)
        if (
            info.st_size != after.st_size
            or info.st_mtime_ns != after.st_mtime_ns
            or info.st_ctime_ns != after.st_ctime_ns
        ):
            raise SecretFileError(f"{flag}: {path} changed while its policy was being read")
        # Re-prove the DACL after the read so a concurrent ACL loosen is caught.
        try:
            assert_owner_only_file_path(
                path,
                purpose=flag,
                require_single_link=require_single_link,
            )
        except SecurePathError as exc:
            raise SecretFileError(
                f"{flag}: {path} changed while its policy was being read"
            ) from exc
    except OSError as exc:
        raise SecretFileError(
            f"{flag}: cannot securely read {path}: {exc.strerror or exc}"
        ) from exc
    finally:
        os.close(descriptor)
    try:
        return b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SecretFileError(f"{flag}: {path} is not valid UTF-8") from exc


def read_regular_file_bytes(
    file_path: str | Path,
    *,
    label: str,
    limit: int = DEFAULT_SECRET_FILE_LIMIT,
) -> bytes:
    """Read one regular file with nofollow semantics (public material; no mode floor).

    Used for pin certificates and public verification documents where owner-only
    mode is not required, but symlinks in the leaf (and on POSIX every path
    component) must still fail closed. The error never includes file content.
    """
    if not _POSIX and not _WINDOWS:
        raise SecretFileError(f"{label}: secure nofollow file open is unavailable on this platform")
    if _POSIX and not hasattr(os, "O_NOFOLLOW"):
        raise SecretFileError(f"{label}: secure nofollow file open is unavailable on this platform")
    path = Path(file_path).expanduser()
    try:
        descriptor = open_nofollow_descriptor(path)
    except OSError as exc:
        raise SecretFileError(
            f"{label}: cannot securely open {path}: {exc.strerror or exc}"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SecretFileError(f"{label}: {path} is not a regular file")
        if info.st_size > limit:
            raise SecretFileError(f"{label}: {path} exceeds the {limit}-byte file limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise SecretFileError(f"{label}: {path} exceeds the {limit}-byte file limit")
    except OSError as exc:
        raise SecretFileError(
            f"{label}: cannot securely read {path}: {exc.strerror or exc}"
        ) from exc
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def read_secret_file(file_path: str | Path, *, flag: str, require_single_link: bool = False) -> str:
    """Read one owner-only secret value, stripped of surrounding whitespace.

    Parameters
    ----------
    file_path : str or pathlib.Path
        File holding exactly one secret value.
    flag : str
        CLI flag being resolved (e.g. ``--metrics-token-file``), named in every
        error so the operator knows which input to fix.
    require_single_link : bool, optional
        Reject a final inode with any other hardlink. Executable policy loaders
        enable this so a repository path cannot alias an outside policy file.

    Returns
    -------
    str
        The secret with surrounding whitespace removed.

    Raises
    ------
    SecretFileError
        If the file is missing, unreadable, empty, or accessible to other
        users. The error never contains file content.
    """
    path = Path(file_path).expanduser()
    secret = _read_owner_only_text(
        path,
        flag=flag,
        require_single_link=require_single_link,
    ).strip()
    if not secret:
        raise SecretFileError(f"{flag}: {path} is empty; expected one secret value")
    return secret


def read_secret_lines(file_path: str | Path, *, flag: str) -> tuple[str, ...]:
    """Read owner-only secret entries, one per line, skipping blanks and comments.

    Parameters
    ----------
    file_path : str or pathlib.Path
        File holding one entry per line. Blank lines and lines starting with
        ``#`` are ignored, so a rotation file can carry dated annotations.
    flag : str
        CLI flag being resolved, named in every error.

    Returns
    -------
    tuple of str
        The entries in file order.

    Raises
    ------
    SecretFileError
        If the file is missing, unreadable, accessible to other users, or
        carries no entries. The error never contains file content.
    """
    path = Path(file_path).expanduser()
    text = _read_owner_only_text(path, flag=flag)
    entries = tuple(
        stripped for line in text.splitlines() if (stripped := line.strip()) and stripped[0] != "#"
    )
    if not entries:
        raise SecretFileError(f"{flag}: {path} holds no entries; expected one per line")
    return entries
