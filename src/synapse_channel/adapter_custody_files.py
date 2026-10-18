# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — file boundaries for recoverable adapter installation
"""Replace adapter files under a trusted root without following target links."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure_path import (
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
)
from synapse_channel.durable_dir import sync_directory
from synapse_channel.opencode_adapter_files import FileSnapshot, read_text_snapshot


class AdapterCustodyError(SynapseError, ValueError):
    """Adapter ownership or a recoverable file transaction cannot be established."""

    code = "adapter_custody"


def validate_directory(path: Path) -> None:
    """Validate an existing adapter directory without changing it.

    Parameters
    ----------
    path : pathlib.Path
        Directory below the explicitly trusted root, or its nearest existing parent.

    Raises
    ------
    AdapterCustodyError, OSError, ValueError
        The directory is linked, foreign-owned, writable by others or fails the
        native Windows owner-only ACL floor.
    """
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise AdapterCustodyError("Adapter parent is not a regular directory.")
    if hasattr(os, "geteuid"):
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise AdapterCustodyError("Adapter parent is not owner-controlled.")
    else:
        assert_owner_only_dir_path(path, purpose="adapter parent")


def validate_target(root: Path, relative: str, *, create: bool) -> Path:
    """Validate the root and each adapter parent, optionally creating missing ones.

    Parameters
    ----------
    root : pathlib.Path
        Explicitly trusted home or project root. Its existing ancestors are
        resolved once; no component below that root may be a symbolic link.
    relative : str
        Catalogue-owned relative target, without parent traversal.
    create : bool
        Create missing parents owner-only. False performs no filesystem writes.

    Returns
    -------
    pathlib.Path
        Lexical target below the resolved root.

    Raises
    ------
    AdapterCustodyError, OSError
        A path is outside the root, linked, replaceable or not owner-controlled.
    """
    part = Path(relative)
    if part.is_absolute() or not part.parts or ".." in part.parts:
        raise AdapterCustodyError("Adapter target must remain below its selected root.")
    anchor = root.expanduser().resolve()
    target = anchor / part
    if anchor.exists():
        validate_directory(anchor)
    missing: list[Path] = []
    current = target.parent
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        current = current.parent
    validate_directory(current)
    if create:
        for directory in reversed(missing):
            directory.mkdir(mode=0o700, exist_ok=True)
            sync_directory(directory.parent)
    current = anchor
    for component in part.parts[:-1]:
        current /= component
        if current.is_symlink():
            raise AdapterCustodyError("Adapter parent is a symbolic link.")
        if current.exists():
            validate_directory(current)
    read_text_snapshot(target)
    if target.exists() and target.lstat().st_nlink != 1:
        raise AdapterCustodyError("Adapter target has multiple hard links.")
    return target


def validate_auxiliary(path: Path, *, allow_readable: bool = False) -> None:
    """Require an existing custody file to be owner-only, regular and singly linked.

    Parameters
    ----------
    path : pathlib.Path
        A lock, journal or replacement file in the private custody directory.
    allow_readable : bool, optional
        Accept a prepared replacement's original read permissions, never write
        access by another user. Its private containing directory remains required.

    Raises
    ------
    AdapterCustodyError, OSError
        The file is linked, exposed, foreign-owned or of another kind.
    """
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AdapterCustodyError("Unsafe adapter custody file.")
    if hasattr(os, "geteuid"):
        forbidden = 0o022 if allow_readable else 0o077
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & forbidden:
            raise AdapterCustodyError("Adapter custody file is not owner-only.")
    else:
        assert_owner_only_file_path(path, purpose="adapter custody")


def snapshot_value(snapshot: FileSnapshot) -> str | None:
    """Distinguish an absent file from an empty existing file.

    Parameters
    ----------
    snapshot : FileSnapshot
        Validated file observation with exact UTF-8 text.

    Returns
    -------
    str or None
        Original text, or None only when the file was absent.
    """
    return snapshot.text if snapshot.existed else None


def clear_replacement(directory: Path) -> None:
    """Remove the fixed transaction scratch file only inside a recorded transaction.

    Parameters
    ----------
    directory : pathlib.Path
        Private directory belonging to the pending custody transaction.

    Raises
    ------
    AdapterCustodyError, OSError
        An existing scratch entry is not an owner-controlled regular single-link file.
    """
    path = directory / "replacement"
    try:
        path.lstat()
    except FileNotFoundError:
        return
    validate_auxiliary(path, allow_readable=True)
    path.unlink()
    sync_directory(directory)


def replace_target(
    target: Path, after: str | None, before: FileSnapshot, *, directory: Path
) -> None:
    """Apply a durably prepared replacement after rechecking the captured target.

    Parameters
    ----------
    target : pathlib.Path
        Validated adapter path.
    after : str or None
        Exact new bytes as UTF-8, or None to remove the captured file.
    before : FileSnapshot
        Snapshot captured while holding the cooperating installers' lock.
    directory : pathlib.Path
        Private journal directory containing the fixed replacement scratch file.

    Raises
    ------
    AdapterCustodyError, OSError
        The target changed or the prepared replacement could not be applied.
        The caller retains its pending journal for explicit reconciliation.
    """
    stage = directory / "replacement"
    if after is not None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(stage, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(after.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        sync_directory(directory)
    if read_text_snapshot(target) != before:
        raise AdapterCustodyError("Adapter target changed during the transaction.")
    if after is None:
        if before.existed:
            target.unlink()
    else:
        stage.chmod(before.mode if before.existed else 0o600)
        with stage.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(stage, target)
        sync_directory(directory)
    sync_directory(target.parent)
