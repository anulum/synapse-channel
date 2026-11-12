# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded durable HTTP operation epoch hints
"""Retain original outgoing fences; only the native hub can confirm a mutation."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from synapse_channel.client.lease_epoch_store import default_lease_epoch_root
from synapse_channel.core.secure_path import apply_owner_only_file, assert_owner_only_file_path
from synapse_channel.durable_dir import private_dir, sync_directory

MAX_OPERATION_EPOCHS = 1024
MAX_OPERATION_CACHE_BYTES = 4 * 1024 * 1024


def _epoch(value: object) -> int | None:
    """Validate a nullable native fence without accepting booleans or conversions."""
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 9223372036854775807:
        raise ValueError("invalid cached HTTP operation epoch")
    return value


def _prepare_cache(path: Path) -> None:
    """Create a private regular cache file without replacing an existing object."""
    private_dir(path.parent, purpose="HTTP operation epoch cache")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
        apply_owner_only_file(path)
        sync_directory(path.parent)
    assert_owner_only_file_path(
        path, purpose="HTTP operation epoch cache", require_single_link=True
    )
    if path.stat().st_size > MAX_OPERATION_CACHE_BYTES:
        raise ValueError("HTTP operation epoch cache exceeds its byte budget")


class HttpOperationEpochStore:
    """Keep bounded owner-local retry hints across adapter processes.

    Parameters
    ----------
    scope : str
        Fixed native identity and operator signing-key binding; never a bearer.
    root : Path or None, optional
        Private cache directory. Default follows the native client's XDG data home.
    limit : int, optional
        Retained operation fences, from one through 1024, oldest evicted first.

    Notes
    -----
    Keys hash hub identity and native operation identity; no token, message body
    or claimed success is stored. SQLite FULL transactions precede native sends.
    A hint can reproduce a request, never authorise or confirm it. Preserve this
    cache with its provisioned identity for replay; evicted or lost hints do not
    establish that an earlier operation failed. Native fencing and idempotency
    remain authoritative. This is not measured power-loss qualification.
    """

    def __init__(
        self, scope: str, *, root: Path | None = None, limit: int = MAX_OPERATION_EPOCHS
    ) -> None:
        if not scope or len(scope) > 8192 or type(limit) is not int or not 1 <= limit <= 1024:
            raise ValueError("invalid HTTP operation epoch cache scope or limit")
        directory = (
            root
            if root is not None
            else default_lease_epoch_root().parent / "http-operation-epochs"
        )
        self.path = directory / (hashlib.sha256(scope.encode()).hexdigest() + ".sqlite3")
        self.limit = limit

    def bind(self, hub_id: str, operation: str, epoch: object) -> int | None:
        """Return the first retained fence, saving a new fence before network dispatch.

        Parameters
        ----------
        hub_id : str
            Actual admitted hub identity, retained across the qualified restart.
        operation : str
            Native message type and caller key; neither selects another principal.
        epoch : object
            Native prepared epoch, or ``None`` when that request supplies none.

        Returns
        -------
        int or None
            Original retained fence, new durable fence, or no known fence.

        Raises
        ------
        ValueError
            On invalid scope, epoch, file budget or stored cache values.
        OSError, sqlite3.Error
            On unavailable, unsafe, busy or corrupt storage. No send follows.
        """
        fence = _epoch(epoch)
        if not hub_id or hub_id == "unknown" or not operation or len(operation) > 512:
            raise ValueError("HTTP operation epoch cache requires an admitted identity")
        key = hashlib.sha256((hub_id + "\0" + operation).encode()).hexdigest()
        _prepare_cache(self.path)
        with closing(sqlite3.connect(self.path, timeout=0.1)) as connection:
            connection.execute("PRAGMA page_size=4096")
            if connection.execute("PRAGMA page_size").fetchone()[0] != 4096:
                raise ValueError("incompatible HTTP operation epoch cache page size")
            connection.execute("PRAGMA max_page_count=1024")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS epochs "
                "(position INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, epoch INTEGER NOT NULL)"
            )
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT count(*) FROM epochs").fetchone()[0] > self.limit:
                    raise ValueError("HTTP operation epoch cache exceeds its row budget")
                row = connection.execute("SELECT epoch FROM epochs WHERE key=?", (key,)).fetchone()
                if row is not None:
                    return _epoch(row[0])
                if fence is None:
                    return None
                connection.execute(
                    "DELETE FROM epochs WHERE position NOT IN "
                    "(SELECT position FROM epochs ORDER BY position DESC LIMIT ?)",
                    (self.limit - 1,),
                )
                connection.execute("INSERT INTO epochs(key,epoch) VALUES (?,?)", (key, fence))
            return fence
