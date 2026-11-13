# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native-session-bound MCP stdio identities
"""Opt in to stable routing names supplied by a supported native MCP client.

A native UUID identifies a local session, not an authenticated hub principal.
Normal hub credentials and authorization still apply to every derived name.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import UUID

from synapse_channel.advisory_lock import locked
from synapse_channel.core.errors import SynapseError
from synapse_channel.durable_dir import sync_directory


class NativeSessionError(SynapseError, ValueError):
    """Refuse a native session whose identifier, prefix or storage is not usable."""

    code = "mcp_native_session"


_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}/[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
_LOCK_PURPOSE = "native MCP session lock directory"
_ABANDONED_LOCK_AGE_SECONDS = 24 * 60 * 60
_ABANDONED_LOCK_SWEEP_LIMIT = 64
_LOCK_NAME = re.compile(r"^[0-9a-f]{64}\.lock$")


def _native_uuid(vendor: str) -> str:
    """Read the supported producer's canonical UUID without echoing bad input."""
    if vendor != "grok":
        raise NativeSessionError("synapse mcp: unsupported native client")
    raw = os.environ.get("GROK_SESSION_ID", "")
    try:
        identifier = UUID(raw)
    except ValueError as exc:
        raise NativeSessionError(
            "synapse mcp: Grok must supply a canonical GROK_SESSION_ID"
        ) from exc
    if str(identifier) != raw or identifier.int == 0:
        raise NativeSessionError("synapse mcp: Grok must supply a canonical GROK_SESSION_ID")
    return raw


def _session_name(prefix: str, identifier: str) -> str:
    """Append the complete UUID to a bounded explicit project/client prefix."""
    if _PREFIX.fullmatch(prefix) is None:
        raise NativeSessionError(
            "synapse mcp: native client requires a bounded project/client prefix"
        )
    return f"{prefix}-{identifier}"


def _lock_path(directory: Path, identity: str) -> Path:
    """Return the lock file for one derived routing name."""
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return directory / f"{digest}.lock"


def _lock_candidates(directory: Path) -> tuple[Path, ...]:
    """Return at most the sweep limit of direct lock-shaped names, in order."""
    chosen: list[Path] = []
    for path in sorted(directory.iterdir()):
        if len(chosen) >= _ABANDONED_LOCK_SWEEP_LIMIT:
            break
        if _LOCK_NAME.fullmatch(path.name) is None:
            continue
        chosen.append(path)
    return tuple(chosen)


def _is_abandoned(path: Path, cutoff: float) -> bool:
    """Say whether ``path`` is a regular file older than the sweep cutoff."""
    try:
        info = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff


def _unlink_held(path: Path) -> None:
    """Remove a lock file while the caller still holds its advisory lock."""
    os.unlink(path)


def _unlink_abandoned(path: Path) -> bool:
    """Unlink ``path`` when no process holds it. Return whether it was removed."""
    try:
        with locked(path, blocking=False, purpose=_LOCK_PURPOSE, removable=True) as acquired:
            if not acquired:
                return False
            _unlink_held(path)
            return True
    except OSError:
        return False


def _sweep_abandoned_locks(directory: Path) -> None:
    """Remove crash-left lock files that nobody holds and that are old enough.

    A crash never reaches the clean-exit unlink, so the file stays. This
    examines at most ``_ABANDONED_LOCK_SWEEP_LIMIT`` direct children whose
    names are 64 hexadecimal characters plus ``.lock``. A file is removed
    only when it is a regular file, its modification time is more than
    ``_ABANDONED_LOCK_AGE_SECONDS`` (24 hours) before this call, and the same
    non-blocking lock used for a live session acquires it. The unlink happens
    while that lock is held. A file another process holds, a younger file, a
    non-matching name, and every name past the limit stays until a later
    session. The age is read before the lock attempt.
    """
    if not directory.is_dir():
        return
    cutoff = time.time() - _ABANDONED_LOCK_AGE_SECONDS
    removed = False
    for path in _lock_candidates(directory):
        if not _is_abandoned(path, cutoff):
            continue
        if _unlink_abandoned(path):
            removed = True
    if removed:
        sync_directory(directory)


@contextmanager
def native_stdio_session(
    name: str, roles: tuple[str, ...], vendor: str | None
) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Hold derived primary and role names until their native bridge exits.

    Native mode requires the client's UUID and never falls back to a shared
    name. Sorted nonblocking OS locks refuse a duplicate on this host while
    the original bridge remains alive. A clean exit unlinks that session's
    lock files while those locks are still held, then releases them. The
    lock is kept only when the path still names that inode, so a name a
    departing holder has unlinked is not treated as owned. A crash
    leaves the files. The next native session removes abandoned lock files
    older than 24 hours that nobody holds, and it does not remove a lock
    another process holds; the sweep is bounded to 64 lock-shaped names.
    Resuming the same native UUID derives the same routing names and inbox
    cursor. All participants must use the same owner-local SYN_HOME. Remote
    ownership remains governed by the hub, and no takeover is requested here.
    On Windows the lock is opened with delete sharing so this unlink can
    succeed while the handle is open. The hosted Windows job runs that opener.
    """
    if vendor is None:
        yield name, roles
        return
    identifier = _native_uuid(vendor)
    primary = _session_name(name, identifier)
    aliases = tuple(_session_name(role, identifier) for role in roles)
    home = Path(os.environ.get("SYN_HOME", str(Path.home() / "synapse"))).expanduser()
    if not home.is_absolute():
        raise NativeSessionError("synapse mcp: native sessions require an absolute SYN_HOME")
    directory = home / "mcp-native-sessions"
    _sweep_abandoned_locks(directory)
    paths = [_lock_path(directory, identity) for identity in sorted(set((primary, *aliases)))]
    with ExitStack() as stack:
        held_paths: list[Path] = []
        try:
            for path in paths:
                try:
                    acquired = stack.enter_context(
                        locked(path, blocking=False, purpose=_LOCK_PURPOSE, removable=True)
                    )
                except OSError as exc:
                    raise RuntimeError("synapse mcp: native session lock is unavailable") from exc
                if not acquired:
                    raise RuntimeError(
                        "synapse mcp: this native session identity is already active"
                    )
                held_paths.append(path)
            yield primary, aliases
        finally:
            for path in held_paths:
                _unlink_held(path)
            if held_paths:
                sync_directory(directory)
