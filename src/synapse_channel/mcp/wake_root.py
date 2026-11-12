# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — aggregate wake root admission
"""Serialize bounded wake-root admission across cooperating processes."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.advisory_lock import locked
from synapse_channel.core.secure_path import (
    apply_owner_only_file,
    assert_owner_only_file_path,
    open_nofollow_leaf,
)
from synapse_channel.durable_dir import private_dir, sync_directory
from synapse_channel.mcp.wake_limits import (
    MAX_ROOT_FILES,
    MAX_WAKE_DATABASE_BYTES,
    MAX_WAKE_JOURNAL_BYTES,
    ROOT_CATALOGUE,
    ROOT_INITIALIZED_LOCK_BYTES,
    ROOT_LOCK,
    WAKE_DATABASE_APPLICATION_ID,
)
from synapse_channel.mcp.wake_pointer import WakeLedgerError, WakeScope
from synapse_channel.mcp.wake_root_catalogue import root_catalogue

_LEDGER_FILE = re.compile(r"([0-9a-f]{64})\.sqlite3(-journal)?")


@dataclass(frozen=True)
class RootInventory:
    """Keep a bounded metadata-only inventory without reading notification bodies."""

    sizes: Mapping[str, int]
    keys: frozenset[str]

    @property
    def logical_bytes(self) -> int:
        """Return actual logical file bytes, separately from reserved write headroom."""
        return sum(self.sizes.values())


def _file_budget(name: str) -> int:
    """Recognize managed main/journal files and the bounded initialization lock."""
    if name == ROOT_LOCK:
        return ROOT_INITIALIZED_LOCK_BYTES
    if name == ROOT_CATALOGUE:
        return MAX_WAKE_DATABASE_BYTES
    if name == ROOT_CATALOGUE + "-journal":
        return MAX_WAKE_JOURNAL_BYTES
    match = _LEDGER_FILE.fullmatch(name)
    if match is None:
        raise WakeLedgerError("unexpected file in wake state root")
    return MAX_WAKE_JOURNAL_BYTES if match.group(2) else MAX_WAKE_DATABASE_BYTES


def scan_wake_root(root: Path) -> RootInventory:
    """Inspect at most the fixed maximum root entries, refusing links and foreign files."""
    sizes: dict[str, int] = {}
    keys: set[str] = set()
    with os.scandir(root) as entries:
        for entry in entries:
            if len(sizes) == MAX_ROOT_FILES:
                raise WakeLedgerError("wake root inventory exceeds its item budget")
            budget = _file_budget(entry.name)
            assert_owner_only_file_path(
                entry.path, purpose="MCP wake root file", require_single_link=True
            )
            size = entry.stat(follow_symlinks=False).st_size
            if size > budget:
                raise WakeLedgerError("wake storage file exceeds its byte budget")
            sizes[entry.name] = size
            match = _LEDGER_FILE.fullmatch(entry.name)
            if match is not None:
                keys.add(match.group(1))
    return RootInventory(sizes, frozenset(keys))


def _prepare_lock(root: Path) -> Path:
    """Create a durable private regular OS-lock file without following another path."""
    path = root / ROOT_LOCK
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
        apply_owner_only_file(path)
        sync_directory(root)
    assert_owner_only_file_path(path, purpose="MCP wake root lock", require_single_link=True)
    if path.stat().st_size > ROOT_INITIALIZED_LOCK_BYTES:
        raise WakeLedgerError("invalid wake root lock")
    return path


def _verify_first_root(root: Path, inventory: RootInventory) -> None:
    """Refuse missing catalogue metadata beside an already root-fenced native database."""
    if ROOT_CATALOGUE in inventory.sizes:
        return
    for key in inventory.keys:
        path = root / (key + ".sqlite3")
        if path.name not in inventory.sizes:
            continue
        fd = open_nofollow_leaf(path)
        try:
            header = os.read(fd, 100)
        finally:
            os.close(fd)
        if (
            len(header) >= 72
            and header[:16] == b"SQLite format 3" + bytes([0])
            and int.from_bytes(header[68:72], "big") == WAKE_DATABASE_APPLICATION_ID
            and int.from_bytes(header[60:64], "big") >= 4
        ):
            raise WakeLedgerError("wake root catalogue metadata is missing")


def _mark_initialized(lock: Path) -> None:
    """Persist initialization outside the first byte reserved for Windows locking."""
    fd = os.open(lock, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "wb") as stream:
        stream.seek(ROOT_INITIALIZED_LOCK_BYTES - 1)
        stream.write(bytes([1]))
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def locked_wake_root(root: Path, *, create: bool = False) -> Iterator[RootInventory]:
    """Initialize metadata before yielding one guarded caller transaction.

    A caller refusal cannot undo completed root initialization. The durable
    second lock byte distinguishes unfinished setup from known metadata loss;
    the first byte remains reserved for the Windows process lock.
    """
    if not create and not root.is_dir():
        raise WakeLedgerError("wake state root unavailable")
    private_dir(root, purpose="MCP wake state root")
    lock = _prepare_lock(root)
    with locked(lock, blocking=False, purpose="MCP wake state root") as held:
        if not held:
            raise WakeLedgerError("wake state root is busy")
        inventory = scan_wake_root(root)
        if inventory.sizes[ROOT_LOCK] < ROOT_INITIALIZED_LOCK_BYTES:
            _verify_first_root(root, inventory)
            with root_catalogue(root, allow_create=ROOT_CATALOGUE not in inventory.sizes):
                pass
            _mark_initialized(lock)
            inventory = scan_wake_root(root)
        elif ROOT_CATALOGUE not in inventory.sizes:
            raise WakeLedgerError("wake root catalogue metadata is missing")
        yield inventory


@contextmanager
def admit_wake_database(path: Path, scope: WakeScope) -> Iterator[None]:
    """Reserve capacity before opening a ledger and publish only successful first commits."""
    if path.name != scope.key + ".sqlite3":
        raise WakeLedgerError("wake database filename does not match its generation")
    root = path.parent
    with locked_wake_root(root, create=True) as inventory:
        with root_catalogue(root) as catalogue:
            if scope.policy_name:
                catalogue.bind_policy(scope.policy_name)
            needs_activation = catalogue.reserve(inventory, scope.key)
        yield
        if needs_activation:
            with root_catalogue(root) as catalogue:
                catalogue.activate(scope.key)
