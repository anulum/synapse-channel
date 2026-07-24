# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded race-aware OpenCode adapter file writes
"""Read, atomically replace, and remove OpenCode-owned files fail closed."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.errors import SynapseError

DEFAULT_FILE_LIMIT = 1_048_576
_UNSAFE_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH

_Fingerprint = tuple[int, int, int, int, int]


class OpenCodeAdapterFileError(SynapseError, OSError):
    """An OpenCode adapter path is unsafe or changed concurrently."""

    code = "opencode_adapter_file"


@dataclass(frozen=True)
class FileSnapshot:
    """Text plus identity metadata used for compare-before-replace writes.

    ``digest`` is the SHA-256 of the file's bytes at snapshot time (``None`` when
    the file did not exist). The write and remove guards verify it in addition to
    the stat ``fingerprint``, so a same-size rewrite that lands within one
    ``mtime``/``ctime`` resolution tick — which stat metadata alone cannot
    distinguish — is still refused.
    """

    text: str
    existed: bool
    fingerprint: _Fingerprint | None
    digest: bytes | None
    mode: int


def _fingerprint(info: os.stat_result) -> _Fingerprint:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _validate_file(path: Path, info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise OpenCodeAdapterFileError(f"OpenCode adapter path is not a regular file: {path}")
    if hasattr(os, "getuid"):
        if info.st_uid != os.getuid():
            raise OpenCodeAdapterFileError(
                f"OpenCode adapter path is not owned by this user: {path}"
            )
        if stat.S_IMODE(info.st_mode) & _UNSAFE_WRITE_BITS:
            raise OpenCodeAdapterFileError(
                f"OpenCode adapter path is writable by group or others: {path}"
            )
    elif os.name == "nt":
        from synapse_channel.core.secure_path import SecurePathError, assert_owner_only_file_path

        try:
            assert_owner_only_file_path(path, purpose="OpenCode adapter file")
        except SecurePathError as exc:
            raise OpenCodeAdapterFileError(
                f"OpenCode adapter path is not owner-only: {path}"
            ) from exc


def _validate_directory_chain(path: Path) -> None:
    """Reject replaceable or symlinked target directories up to a trusted ancestor."""
    current = path
    found_existing = False
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            parent = current.parent
            if parent == current:
                raise OpenCodeAdapterFileError(
                    f"OpenCode adapter has no existing parent directory: {path}"
                ) from None
            current = parent
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OpenCodeAdapterFileError(f"OpenCode adapter parent is unsafe: {current}")
        mode = stat.S_IMODE(info.st_mode)
        if not found_existing:
            found_existing = True
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise OpenCodeAdapterFileError(
                    f"OpenCode adapter parent is not owned by this user: {current}"
                )
        if hasattr(os, "getuid"):
            if info.st_uid == os.getuid():
                if mode & _UNSAFE_WRITE_BITS:
                    raise OpenCodeAdapterFileError(
                        f"OpenCode adapter parent is writable by group or others: {current}"
                    )
            else:
                if mode & _UNSAFE_WRITE_BITS and not mode & stat.S_ISVTX:
                    raise OpenCodeAdapterFileError(
                        f"OpenCode adapter ancestor is writable by group or others: {current}"
                    )
            return
        parent = current.parent
        if parent == current:
            return
        current = parent


def _mkdir_private_parents(path: Path) -> None:
    """Create each missing parent with owner-only permissions."""
    _validate_directory_chain(path)
    missing: list[Path] = []
    current = path
    while True:
        try:
            current.lstat()
            break
        except FileNotFoundError:
            missing.append(current)
            parent = current.parent
            if parent == current:
                raise OpenCodeAdapterFileError(
                    f"OpenCode adapter has no existing parent directory: {path}"
                ) from None
            current = parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
    _validate_directory_chain(path)


def _read_validated(path: Path, limit: int) -> tuple[os.stat_result, bytes]:
    """Open, validate, and read one owner-controlled file without following symlinks.

    Opens ``path`` with ``O_NOFOLLOW``, validates the opened file, and reads its
    bounded content, returning the post-open ``fstat`` and the raw bytes.
    """
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        _validate_file(path, info)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise OpenCodeAdapterFileError(
                    f"OpenCode adapter file exceeds the {limit}-byte edit limit: {path}"
                )
    finally:
        os.close(descriptor)
    return info, b"".join(chunks)


def read_text_snapshot(path: Path, *, limit: int = DEFAULT_FILE_LIMIT) -> FileSnapshot:
    """Read one owner-controlled regular UTF-8 file without following its leaf symlink."""
    _validate_directory_chain(path.parent)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return FileSnapshot("", False, None, None, 0o600)
    _validate_file(path, before)
    after, data = _read_validated(path, limit)
    if _fingerprint(before) != _fingerprint(after):
        raise OpenCodeAdapterFileError(f"OpenCode adapter path changed while opening: {path}")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OpenCodeAdapterFileError(f"OpenCode adapter file is not UTF-8: {path}") from exc
    return FileSnapshot(
        text, True, _fingerprint(after), hashlib.sha256(data).digest(), stat.S_IMODE(after.st_mode)
    )


def _assert_current(path: Path, snapshot: FileSnapshot) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        if snapshot.existed:
            raise OpenCodeAdapterFileError(f"OpenCode adapter path disappeared: {path}") from None
        return
    if not snapshot.existed:
        raise OpenCodeAdapterFileError(f"OpenCode adapter path appeared concurrently: {path}")
    _validate_file(path, current)
    # Re-read the content and compare its digest as well as the stat fingerprint:
    # a same-size rewrite within one mtime/ctime tick leaves the fingerprint
    # identical, so only the digest catches it.
    info, data = _read_validated(path, DEFAULT_FILE_LIMIT)
    if (
        snapshot.fingerprint != _fingerprint(info)
        or snapshot.digest != hashlib.sha256(data).digest()
    ):
        raise OpenCodeAdapterFileError(f"OpenCode adapter path changed concurrently: {path}")


def _fsync_directory(path: Path) -> None:
    """Durably flush directory metadata when the platform supports it.

    Windows refuses ``os.open`` on directories, so directory fsync is skipped
    after the file itself has already been fsynced.
    """
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_text_snapshot(path: Path, text: str, snapshot: FileSnapshot) -> None:
    """Atomically write ``text`` only while ``snapshot`` remains current."""
    data = text.encode("utf-8")
    if len(data) > DEFAULT_FILE_LIMIT:
        raise OpenCodeAdapterFileError(f"Updated OpenCode adapter file is too large: {path}")
    _mkdir_private_parents(path.parent)
    _assert_current(path, snapshot)
    from synapse_channel.core.secure_path import apply_owner_only_file

    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, snapshot.mode if snapshot.existed else 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_current(path, snapshot)
        os.replace(temporary, path)
        # POSIX already has fchmod for mode preservation; Windows needs DACL.
        if not hasattr(os, "fchmod"):
            apply_owner_only_file(path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def remove_snapshot(path: Path, snapshot: FileSnapshot) -> None:
    """Remove ``path`` only while its captured identity is unchanged."""
    if not snapshot.existed:
        return
    _validate_directory_chain(path.parent)
    _assert_current(path, snapshot)
    path.unlink()
    _fsync_directory(path.parent)
