# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox-count tables, layout version, and copy
"""Create the mailbox-count tables and refuse a layout this code does not know.

The tables share the event store's one connection. Version 1 is one row. That
row also records the highest event sequence and the number of chat rows the
tables reflect, and the highest sequence the chats' name mentions reflect. A
writer that keeps hits but not mentions leaves that last figure behind, and a
stale figure is ``-1``. A newer version is refused before any table is
rewritten. A finished layout can be copied onto a converted store so that store
does not walk again.
"""

from __future__ import annotations

from typing import Any

from synapse_channel.core.mailbox_layout_mentions import copy_mentions

LAYOUT_VERSION = 1
"""The only mailbox-count layout this code opens."""

_CHAT = "chat"
_STALE = -1


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
        "ON mailbox_address_hits(seq) WHERE "
        "(instr(address, '?') > 0 OR instr(address, '[') > 0 OR instr(address, '*') > 0)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS mailbox_pending_counts ("
        "identity TEXT PRIMARY KEY, pending INTEGER NOT NULL, watermark INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS mailbox_pending_counts_role "
        "ON mailbox_pending_counts(identity) WHERE lower(substr(identity, 1, 5)) = 'role:'"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS mailbox_name_mentions ("
        "seq INTEGER NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL, "
        "PRIMARY KEY(seq, position))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS mailbox_pending_layout ("
        "version INTEGER NOT NULL, projected_through INTEGER NOT NULL DEFAULT 0, "
        "chat_rows INTEGER NOT NULL DEFAULT 0, mentions_through INTEGER NOT NULL DEFAULT -1)"
    )
    _add_known_columns(connection)


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
    through, chats = _copied_extent(source)
    current = copy_mentions(source, destination) and _copied_mentions_current(source)
    destination.execute("DELETE FROM mailbox_pending_layout")
    destination.execute(
        "INSERT INTO mailbox_pending_layout "
        "(version, projected_through, chat_rows, mentions_through) VALUES (?, ?, ?, ?)",
        (LAYOUT_VERSION, through, chats, through if current else _STALE),
    )


def _add_known_columns(connection: Any) -> None:
    """Add each column this code already knows, using that column's own statement."""
    counts = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(mailbox_pending_counts)")
    }
    if "pending" not in counts:
        connection.execute(
            "ALTER TABLE mailbox_pending_counts ADD COLUMN pending INTEGER NOT NULL DEFAULT 0"
        )
    if "watermark" not in counts:
        connection.execute(
            "ALTER TABLE mailbox_pending_counts ADD COLUMN watermark INTEGER NOT NULL DEFAULT 0"
        )
    hits = {str(row[1]) for row in connection.execute("PRAGMA table_info(mailbox_address_hits)")}
    if "sender" not in hits:
        connection.execute(
            "ALTER TABLE mailbox_address_hits ADD COLUMN sender TEXT NOT NULL DEFAULT ''"
        )
    layout = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(mailbox_pending_layout)")
    }
    if "projected_through" not in layout:
        connection.execute(
            "ALTER TABLE mailbox_pending_layout ADD COLUMN projected_through "
            "INTEGER NOT NULL DEFAULT 0"
        )
    if "chat_rows" not in layout:
        connection.execute(
            "ALTER TABLE mailbox_pending_layout ADD COLUMN chat_rows INTEGER NOT NULL DEFAULT 0"
        )
    if "mentions_through" not in layout:
        connection.execute(
            "ALTER TABLE mailbox_pending_layout ADD COLUMN mentions_through "
            "INTEGER NOT NULL DEFAULT -1"
        )


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


def _journal_extent(connection: Any) -> tuple[int, int]:
    """Return the highest event sequence and the number of chat rows."""
    maximum = connection.execute("SELECT MAX(seq) FROM events").fetchone()
    chats = connection.execute("SELECT COUNT(*) FROM events WHERE kind = ?", (_CHAT,)).fetchone()
    high = 0 if maximum is None or maximum[0] is None else maximum[0]
    count = 0 if chats is None or chats[0] is None else chats[0]
    pair = _extent_pair(high, count)
    return (0, 0) if pair is None else pair


def _layout_matches(connection: Any) -> bool:
    """Return whether the stored extent is the journal's extent."""
    stored = _stored_extent(connection)
    return stored is not None and stored == _journal_extent(connection)


def _stored_extent(connection: Any) -> tuple[int, int] | None:
    """Return the stored extent, or ``None`` when no usable row is stored.

    The outer join always yields one row, so a layout row that another process
    removed after the version was read reads as an absent extent.
    """
    row = connection.execute(
        "SELECT layout.projected_through, layout.chat_rows FROM (SELECT 1) "
        "LEFT JOIN (SELECT projected_through, chat_rows FROM mailbox_pending_layout LIMIT 1) "
        "AS layout ON 1"
    ).fetchone()
    return _extent_pair(row[0], row[1])


def _mentions_current(connection: Any) -> bool:
    """Return whether the stored mentions reflect every event the extent names.

    The outer join always yields one row, as in :func:`_stored_extent`.
    """
    row = connection.execute(
        "SELECT layout.projected_through, layout.mentions_through FROM (SELECT 1) "
        "LEFT JOIN (SELECT projected_through, mentions_through FROM mailbox_pending_layout "
        "LIMIT 1) AS layout ON 1"
    ).fetchone()
    pair = _extent_pair(row[0], row[1])
    return pair is not None and pair[0] == pair[1]


def _advance_layout_extent(connection: Any, seq: int, kind: str) -> None:
    """Move the stored extent after one insert, when a layout row already exists.

    The mentions figure moves with it only while it was current, so a gap left by
    a writer that keeps no mentions stays visible until the next walk.
    """
    row = connection.execute(
        "SELECT projected_through, chat_rows, mentions_through FROM mailbox_pending_layout LIMIT 1"
    ).fetchone()
    if row is None:
        return
    stored = _extent_pair(row[0], row[1])
    through, chats = (0, 0) if stored is None else stored
    current = stored is not None and _extent_pair(through, row[2]) == (through, through)
    if kind == _CHAT:
        chats += 1
    advanced = max(through, int(seq))
    connection.execute(
        "UPDATE mailbox_pending_layout SET projected_through = ?, chat_rows = ?, "
        "mentions_through = ?",
        (advanced, chats, advanced if current else _STALE),
    )


def _refresh_layout_extent(connection: Any) -> None:
    """Replace the stored extent from the journal, when a layout row already exists."""
    row = connection.execute("SELECT 1 FROM mailbox_pending_layout LIMIT 1").fetchone()
    if row is None:
        return
    _write_layout_extent(connection, mentions_current=_mentions_current(connection))


def _write_layout_extent(connection: Any, *, mentions_current: bool = True) -> None:
    """Store version 1 and the journal's current extent.

    The mentions figure is that extent when the mentions are current, else stale.
    """
    through, chats = _journal_extent(connection)
    connection.execute("DELETE FROM mailbox_pending_layout")
    connection.execute(
        "INSERT INTO mailbox_pending_layout "
        "(version, projected_through, chat_rows, mentions_through) VALUES (?, ?, ?, ?)",
        (LAYOUT_VERSION, through, chats, through if mentions_current else _STALE),
    )


def _copied_mentions_current(source: Any) -> bool:
    columns = {str(row[1]) for row in source.execute("PRAGMA table_info(mailbox_pending_layout)")}
    if "projected_through" not in columns or "mentions_through" not in columns:
        return False
    return _mentions_current(source)


def _copied_extent(source: Any) -> tuple[int, int]:
    columns = {str(row[1]) for row in source.execute("PRAGMA table_info(mailbox_pending_layout)")}
    if "projected_through" not in columns or "chat_rows" not in columns:
        return 0, 0
    stored = _stored_extent(source)
    return (0, 0) if stored is None else stored


def _extent_pair(through: object, chats: object) -> tuple[int, int] | None:
    if isinstance(through, bool) or not isinstance(through, int):
        return None
    if isinstance(chats, bool) or not isinstance(chats, int):
        return None
    return through, chats


def _refuse_version(version: int) -> None:
    raise MailboxLayoutRefused(
        f"unsupported mailbox pending layout version {version}; "
        f"this build understands version {LAYOUT_VERSION} and does not change the tables"
    )
