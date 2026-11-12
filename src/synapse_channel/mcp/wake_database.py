# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private bounded wake ledger storage
"""Own SQLite durability, file privacy and authority binding for wake pointers."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from synapse_channel.core.secure_path import apply_owner_only_file, assert_owner_only_file_path
from synapse_channel.durable_dir import private_dir, sync_directory
from synapse_channel.mcp.wake_limits import (
    MAX_WAKE_DATABASE_BYTES as MAX_WAKE_DATABASE_BYTES,
)
from synapse_channel.mcp.wake_limits import MAX_WAKE_RECEIPTS as MAX_WAKE_RECEIPTS
from synapse_channel.mcp.wake_limits import WAKE_DATABASE_APPLICATION_ID
from synapse_channel.mcp.wake_pointer import (
    MAX_WAKE_BODY_BYTES,
    MAX_WAKE_INTEGER,
    WakeLedgerError,
    WakeScope,
)
from synapse_channel.mcp.wake_root import admit_wake_database
from synapse_channel.mcp.wake_sqlite import configure_wake_sqlite

_APPLICATION_ID = WAKE_DATABASE_APPLICATION_ID
_SCHEMA = (
    "CREATE TABLE meta(scope TEXT PRIMARY KEY,hub TEXT NOT NULL,"
    "revision INTEGER NOT NULL DEFAULT 0,pending INTEGER NOT NULL DEFAULT 0,"
    "accepted INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,"
    "retired INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE sources(source TEXT PRIMARY KEY,position TEXT NOT NULL)",
    "CREATE TABLE deliveries(ordinal INTEGER PRIMARY KEY AUTOINCREMENT,"
    "id TEXT UNIQUE NOT NULL,revision INTEGER NOT NULL,bits INTEGER NOT NULL,"
    "body BLOB NOT NULL,state TEXT NOT NULL,attempt INTEGER NOT NULL,"
    "deadline INTEGER NOT NULL,due INTEGER NOT NULL,status INTEGER NOT NULL DEFAULT 0,"
    "cycle_base INTEGER NOT NULL DEFAULT 0,control_action TEXT NOT NULL DEFAULT '',"
    "control_at INTEGER NOT NULL DEFAULT 0)",
    "CREATE UNIQUE INDEX active_delivery ON deliveries((1)) WHERE state IN ('inflight','retry')",
)


def _prepare(path: Path) -> bool:
    """Create an owner-only regular ledger, refusing links and oversized inputs."""
    private_dir(path.parent, purpose="MCP wake ledger")
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
        apply_owner_only_file(path)
        sync_directory(path.parent)
    assert_owner_only_file_path(path, purpose="MCP wake ledger", require_single_link=True)
    size = path.stat().st_size
    if size > MAX_WAKE_DATABASE_BYTES:
        raise WakeLedgerError("wake ledger exceeds its byte budget")
    return size == 0


def _verify(connection: sqlite3.Connection, scope: WakeScope) -> None:
    """Refuse foreign scopes, schemas and receipt populations before mutation."""
    row = connection.execute("SELECT * FROM meta").fetchall()
    if len(row) != 1 or (row[0]["scope"], row[0]["hub"]) != (scope.key, scope.hub_id):
        raise WakeLedgerError("wake ledger identity changed")
    counters = [row[0][name] for name in ("revision", "pending", "accepted", "failed", "retired")]
    if (
        any(type(value) is not int or not 0 <= value <= MAX_WAKE_INTEGER for value in counters)
        or row[0]["pending"] > 7
    ):
        raise WakeLedgerError("invalid wake ledger counters")
    count = connection.execute("SELECT count(*) FROM deliveries").fetchone()[0]
    bad = connection.execute(
        "SELECT count(*) FROM deliveries WHERE state NOT IN "
        "('inflight','retry','accepted','dead-letter','retired') OR length(body)>?",
        (MAX_WAKE_BODY_BYTES,),
    ).fetchone()[0]
    active = connection.execute(
        "SELECT count(*) FROM deliveries WHERE state IN ('inflight','retry')"
    ).fetchone()[0]
    if count > MAX_WAKE_RECEIPTS or active > 1 or bad:
        raise WakeLedgerError("invalid wake ledger receipt population")


def _configure(connection: sqlite3.Connection, *, fresh: bool) -> int:
    """Require the supported schema and bound page, temporary and journal behavior."""
    version: int = 0 if fresh else connection.execute("PRAGMA user_version").fetchone()[0]
    if not fresh and (
        connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
        or version not in (1, 2, 3, 4)
    ):
        raise WakeLedgerError("unsupported wake ledger schema")
    configure_wake_sqlite(connection)
    return version


def _migrate(connection: sqlite3.Connection, scope: WakeScope, version: int) -> None:
    """Retain prior observer data while fencing writers that do not honor root admission."""
    if version == 0:
        for statement in _SCHEMA:
            connection.execute(statement)
        connection.execute("INSERT INTO meta(scope,hub) VALUES (?,?)", (scope.key, scope.hub_id))
        connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
    elif version == 1:
        for field in (
            "cycle_base INTEGER NOT NULL DEFAULT 0",
            "control_action TEXT NOT NULL DEFAULT ''",
            "control_at INTEGER NOT NULL DEFAULT 0",
        ):
            connection.execute("ALTER TABLE deliveries ADD COLUMN " + field)
    if version != 4:
        connection.execute("PRAGMA user_version=4")


@contextmanager
def wake_database(path: Path, scope: WakeScope) -> Iterator[sqlite3.Connection]:
    """Commit one private bounded transaction under aggregate root admission.

    FULL/DELETE commits retain process-restart custody. The root lock serializes
    cooperating writers and reserves catalogue/rollback headroom before mutation;
    it is not an OS quota or a hardware power-loss qualification. Old binaries
    must be stopped before upgrading or archiving a root.
    """
    try:
        with admit_wake_database(path, scope):
            fresh = _prepare(path)
            with closing(sqlite3.connect(path, timeout=0.1)) as connection:
                connection.row_factory = sqlite3.Row
                version = _configure(connection, fresh=fresh)
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    _migrate(connection, scope, version)
                    _verify(connection, scope)
                    yield connection
                    _verify(connection, scope)
    except (OSError, sqlite3.Error, ValueError):
        raise WakeLedgerError("wake ledger unavailable") from None
