# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded race-aware OpenCode adapter file writes
"""Read, atomically replace, and remove OpenCode-owned files fail closed."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.errors import SynapseError

DEFAULT_FILE_LIMIT = 1_048_576


class OpenCodeAdapterFileError(SynapseError, OSError):
    """An OpenCode adapter path is unsafe or changed concurrently."""

    code = "opencode_adapter_file"


@dataclass(frozen=True)
class FileSnapshot:
    """Text plus identity metadata used for compare-before-replace writes."""

    text: str
    existed: bool
    fingerprint: tuple[int, int, int, int] | None
    mode: int


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _validate_file(path: Path, info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise OpenCodeAdapterFileError(f"OpenCode adapter path is not a regular file: {path}")
    if info.st_uid != os.getuid():
        raise OpenCodeAdapterFileError(f"OpenCode adapter path is not owned by this user: {path}")


def read_text_snapshot(path: Path, *, limit: int = DEFAULT_FILE_LIMIT) -> FileSnapshot:
    """Read one owner-controlled regular UTF-8 file without following its leaf symlink."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return FileSnapshot("", False, None, 0o600)
    _validate_file(path, before)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        after = os.fstat(descriptor)
        _validate_file(path, after)
        if _fingerprint(before) != _fingerprint(after):
            raise OpenCodeAdapterFileError(f"OpenCode adapter path changed while opening: {path}")
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
    try:
        text = b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OpenCodeAdapterFileError(f"OpenCode adapter file is not UTF-8: {path}") from exc
    return FileSnapshot(text, True, _fingerprint(after), stat.S_IMODE(after.st_mode))


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
    if snapshot.fingerprint != _fingerprint(current):
        raise OpenCodeAdapterFileError(f"OpenCode adapter path changed concurrently: {path}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_text_snapshot(path: Path, text: str, snapshot: FileSnapshot) -> None:
    """Atomically write ``text`` only while ``snapshot`` remains current."""
    data = text.encode("utf-8")
    if len(data) > DEFAULT_FILE_LIMIT:
        raise OpenCodeAdapterFileError(f"Updated OpenCode adapter file is too large: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise OpenCodeAdapterFileError(f"OpenCode adapter parent is unsafe: {path.parent}")
    _assert_current(path, snapshot)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, snapshot.mode if snapshot.existed else 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_current(path, snapshot)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def remove_snapshot(path: Path, snapshot: FileSnapshot) -> None:
    """Remove ``path`` only while its captured identity is unchanged."""
    if not snapshot.existed:
        return
    _assert_current(path, snapshot)
    path.unlink()
    _fsync_directory(path.parent)
