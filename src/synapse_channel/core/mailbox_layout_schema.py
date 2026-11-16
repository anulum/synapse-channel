# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox-count tables, layout version, and copy
"""Create the mailbox-count tables and refuse a layout this code does not know.

The tables share the event store's one connection. Version 1 is one row. A
newer version is refused before any table is rewritten. A finished layout can
be copied onto a converted store so that store does not walk again.
"""

from __future__ import annotations

from typing import Any

LAYOUT_VERSION = 1
"""The only mailbox-count layout this code opens."""

GLOB_SQL = "(instr(address, '?') > 0 OR instr(address, '[') > 0 OR instr(address, '*') > 0)"
"""Partial index and count predicate for glob target parts."""

_KNOWN_COLUMNS = (
    ("mailbox_pending_counts", "pending", "INTEGER NOT NULL DEFAULT 0"),
    ("mailbox_pending_counts", "watermark", "INTEGER NOT NULL DEFAULT 0"),
    ("mailbox_address_hits", "sender", "TEXT NOT NULL DEFAULT ''"),
)


class MailboxLayoutRefused(ValueError):
    """The mailbox-count tables are a layout this process must not interpret."""


def ensure_mailbox_schema(connection: Any) -> None:
    """Create the count tables when this store does not have them yet.

    An existing table is never dropped. A column this code already knows how to
    read is added when an older table lacks it. Nothing else is rewritten.

    Parameters
    ----------
    connection : Any
        The event store's own connection.
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS mailbox_address_hits ("
        "seq INTEGER NOT NULL, address TEXT NOT NULL, sender TEXT NOT NULL, "
        "PRIMARY KEY(seq, address))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS mailbox_address_hits_address_seq "
        "ON mailbox_address_hits(address, seq)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS mailbox_address_hits_glob_seq "
        "ON mailbox_address_hits(seq) WHERE " + GLOB_SQL
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS mailbox_pending_counts ("
        "identity TEXT PRIMARY KEY, pending INTEGER NOT NULL, watermark INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS mailbox_pending_layout (version INTEGER NOT NULL)"
    )
    for table, column, declaration in _KNOWN_COLUMNS:
        _add_known_column(connection, table, column, declaration)


def refuse_unknown_mailbox_layout(connection: Any) -> None:
    """Refuse a layout version this code does not know, and change nothing.

    Parameters
    ----------
    connection : Any
        The event store's own connection.

    Raises
    ------
    MailboxLayoutRefused
        When the stored version is newer than :data:`LAYOUT_VERSION`, or is not
        an integer. The tables are left as they are.
    """
    version = _stored_version(connection)
    if version is None or version <= LAYOUT_VERSION:
        return
    _refuse_version(version)


def layout_version(connection: Any) -> int:
    """Return the stored layout version, or 0 when the store has not been projected.

    Parameters
    ----------
    connection : Any
        The event store's own connection.

    Returns
    -------
    int
        ``0`` when no version row exists, otherwise the stored integer.
    """
    ensure_mailbox_schema(connection)
    version = _stored_version(connection)
    return 0 if version is None else version


def require_known_layout(connection: Any) -> int:
    """Return the layout version after refusing one this code does not know.

    Parameters
    ----------
    connection : Any
        The event store's own connection.

    Returns
    -------
    int
        ``0`` or :data:`LAYOUT_VERSION`.
    """
    refuse_unknown_mailbox_layout(connection)
    return layout_version(connection)


def copy_mailbox_layout(source: Any, destination: Any) -> bool:
    """Copy a finished layout so a converted store does not walk again.

    A source that has not been projected is left out of ``destination``. The
    converted store's first open then walks the copied events. Rows keep their
    sequence numbers, which the copied hits still name. The caller commits.

    Parameters
    ----------
    source : Any
        The plaintext store connection.
    destination : Any
        The new store connection.

    Returns
    -------
    bool
        ``True`` when a version-1 layout was copied.
    """
    if _stored_version(source) != LAYOUT_VERSION:
        return False
    ensure_mailbox_schema(destination)
    _copy_rows(source, destination)
    return True


def _copy_rows(source: Any, destination: Any) -> None:
    destination.executemany(
        "INSERT INTO mailbox_address_hits (seq, address, sender) VALUES (?, ?, ?)",
        list(source.execute("SELECT seq, address, sender FROM mailbox_address_hits ORDER BY seq")),
    )
    destination.executemany(
        "INSERT INTO mailbox_pending_counts (identity, pending, watermark) VALUES (?, ?, ?)",
        list(source.execute("SELECT identity, pending, watermark FROM mailbox_pending_counts")),
    )
    destination.execute("DELETE FROM mailbox_pending_layout")
    destination.execute(
        "INSERT INTO mailbox_pending_layout (version) VALUES (?)",
        (LAYOUT_VERSION,),
    )


def _add_known_column(connection: Any, table: str, column: str, declaration: str) -> None:
    present = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in present:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _stored_version(connection: Any) -> int | None:
    names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "mailbox_pending_layout" not in names:
        return None
    row = connection.execute("SELECT version FROM mailbox_pending_layout LIMIT 1").fetchone()
    if row is None:
        return None
    version = row[0]
    if isinstance(version, bool) or not isinstance(version, int):
        raise MailboxLayoutRefused("mailbox pending layout version is not an integer")
    return version


def _refuse_version(version: int) -> None:
    raise MailboxLayoutRefused(
        f"unsupported mailbox pending layout version {version}; "
        f"this build understands version {LAYOUT_VERSION} and does not change the tables"
    )
