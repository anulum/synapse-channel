# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private native-composer readback files
"""Keep native editor probes bounded and private without changing a draft."""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.core.secret_files import open_nofollow_descriptor, read_secret_file
from synapse_channel.core.secure_path import assert_owner_only_dir_path

DOCUMENT_LIMIT = 16_384
DRAFT_LIMIT = 65_536


class ComposerError(SynapseError, ValueError):
    """Report a refused native probe without exposing its input."""

    code = "native_composer"


def read_document(path: Path) -> dict[str, object]:
    """Read bounded JSON from a private, single-link, no-follow file.

    Parameters
    ----------
    path : pathlib.Path
        Owner-only protocol document, limited to DOCUMENT_LIMIT bytes.

    Returns
    -------
    dict of str to object
        Decoded JSON object. Individual consumers validate its schema.

    Raises
    ------
    ValueError, OSError
        The file, its permissions, size, links or JSON object are invalid.
    """
    value: object = json.loads(
        read_secret_file(
            path, flag="native composer", limit=DOCUMENT_LIMIT, require_single_link=True
        )
    )
    if not isinstance(value, dict):
        raise ComposerError("native composer document is not an object")
    return cast(dict[str, object], value)


def _private_descriptor(root: Path) -> int:
    """Open and validate the same owner-only directory descriptor."""
    descriptor = open_nofollow_descriptor(root, directory=True)
    info = os.fstat(descriptor)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        os.close(descriptor)
        raise ComposerError("native composer directory is not private")
    return descriptor


def write_document(root: Path, name: str, document: Mapping[str, object]) -> None:
    """Durably replace one small document beneath a pinned private directory.

    Parameters
    ----------
    root : pathlib.Path
        Existing owner-only profile directory.
    name : str
        Local JSON document name accepted by write_private_bytes.
    document : mapping of str to object
        JSON-serialisable metadata, without native draft text.

    Raises
    ------
    ValueError, OSError
        Serialization, directory validation or the bounded atomic write fails.
    """
    data = (json.dumps(dict(document), sort_keys=True) + "\n").encode()
    write_private_bytes(root, name, data)


def write_private_bytes(root: Path, name: str, data: bytes) -> None:
    """Durably replace one bounded profile document or fixed editor launcher.

    Parameters
    ----------
    root : pathlib.Path
        Existing owner-only directory opened without following a symlink.
    name : str
        Lowercase local basename ending in .json or .py.
    data : bytes
        Complete replacement, no larger than DOCUMENT_LIMIT.

    Raises
    ------
    ComposerError
        The basename, size or directory privacy is invalid.
    OSError
        Creating, syncing or replacing the private file fails.

    Notes
    -----
    The temporary file and replacement use the same pinned directory descriptor.
    A completed write syncs both the file and its containing directory.
    """
    if not re.fullmatch(r"[a-z][a-z-]*\.(?:json|py)", name):
        raise ComposerError("native composer document name is invalid")
    if len(data) > DOCUMENT_LIMIT:
        raise ComposerError("native composer document exceeds its bound")
    descriptor = _private_descriptor(root)
    temporary = f".probe-{uuid.uuid4().hex}"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=descriptor)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            os.fsync(descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
    finally:
        os.close(descriptor)


@contextmanager
def probe_lock(root: Path, *, name: str = "probe.lock") -> Iterator[None]:
    """Exclusively own one finite readback without waiting on another waker.

    Parameters
    ----------
    root : pathlib.Path
        Owner-only native profile directory.
    name : str, optional
        probe.lock for one readback or wake.lock for a whole wake/startup.

    Yields
    ------
    None
        Exclusive ownership, released when the context exits.

    Raises
    ------
    ComposerError
        The platform, lock entry or name is invalid, or another holder exists.
    OSError
        The private directory or no-follow lock cannot be opened.
    """
    if name not in {"probe.lock", "wake.lock"}:
        raise ComposerError("native composer lock name is invalid")
    if os.name != "posix":
        raise ComposerError("native editor readback requires POSIX")
    import fcntl

    ensure_private_dir(root, purpose="native composer")
    parent = _private_descriptor(root)
    try:
        fd = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ComposerError("native composer lock is not private")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ComposerError("another native composer probe is active") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_native_draft(root: Path, path: Path) -> bytes:
    """Read the native export once; refuse links, foreign files and mutation.

    Parameters
    ----------
    root : pathlib.Path
        Profile containing the owner-only tmp directory.
    path : pathlib.Path
        Native numeric .md filename directly beneath that directory.

    Returns
    -------
    bytes
        Exact export, at most DRAFT_LIMIT bytes, without decoding or trimming.

    Raises
    ------
    ComposerError
        The path, ownership, file type, link count or size is invalid, or the
        export changes during the read.
    OSError, ValueError
        The private path or no-follow descriptor cannot be read safely.
    """
    temporary = root / "tmp"
    if path.parent != temporary or not re.fullmatch(r"[0-9]{1,24}\.md", path.name):
        raise ComposerError("native draft is outside the configured temporary directory")
    assert_owner_only_dir_path(temporary, purpose="native composer temporary directory")
    fd = open_nofollow_descriptor(path)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_size > DRAFT_LIMIT
        ):
            raise ComposerError("native draft is not an owned bounded regular file")
        chunks: list[bytes] = []
        count = 0
        while True:
            chunk = os.read(fd, min(8192, DRAFT_LIMIT + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
            if count > DRAFT_LIMIT:
                raise ComposerError("native draft exceeds its bound")
        after = os.fstat(fd)
        if (
            before.st_size != count
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise ComposerError("native draft changed during readback")
        return b"".join(chunks)
    finally:
        os.close(fd)
