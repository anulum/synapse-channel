# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — event-store format floor
"""Refuse an event store this release cannot read, and record the format it writes.

The SQLite header field ``user_version`` of an event store holds its format floor:
the lowest store format a reader must understand to read that store correctly.
This release writes :data:`STORE_FORMAT` and reads every format up to it. A
release raises :data:`STORE_FORMAT` only when it writes something an older reader
would misread: an event kind whose omission changes a decision, or a column whose
absence changes a result. An addition that an older reader may safely ignore
keeps the format.

The check reads the header and changes nothing, so a refused store keeps every
byte. Releases from before the first one that records a floor do not read the
field; a downgrade below that release is not guarded, and the only way back
across a raised floor is a copy of the store taken before the upgrade.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

STORE_FORMAT = 1
"""The store format this release writes, and the highest one it reads.

Format 1 is the event-store layout of the first release that records a floor.
"""

_RECORD_TABLE = "store_format"


class StoreFormatRefused(ValueError):
    """The event store needs a newer store format than this release reads.

    Parameters
    ----------
    path : str
        The refused store's path, for the operator.
    floor : int
        The store's recorded format floor.
    floor_release : str or None
        The release that raised the floor to that value, when the store records it.
    reader_release : str
        The release that refused the store.
    """

    def __init__(
        self, *, path: str, floor: int, floor_release: str | None, reader_release: str
    ) -> None:
        self.path = path
        self.floor = floor
        self.floor_release = floor_release
        self.reader_release = reader_release
        writer = f"release {floor_release}" if floor_release else "an unnamed release"
        super().__init__(
            f"event store {path} needs store format {floor}, set by {writer}; "
            f"release {reader_release} reads store formats up to {STORE_FORMAT}. "
            "The store is left unchanged. Run a release that reads this format, "
            "or restore the copy of the store taken before the upgrade."
        )


def stored_store_format(connection: Any) -> int:
    """Return the store's format floor, reading only the header.

    Parameters
    ----------
    connection : connection
        An open SQLite or SQLCipher DB-API connection to the event store.

    Returns
    -------
    int
        The recorded floor; ``0`` for a store no floor-recording release has opened.

    Raises
    ------
    ValueError
        When the header field is negative, which no release writes.
    """
    value = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if value < 0:
        raise ValueError(f"event store format floor {value} is negative")
    return value


def _stored_record(connection: Any) -> tuple[object, object, object] | None:
    """Return the record's floor and release names, or ``None`` without the table.

    The table is looked up in the schema first, so the same code serves the stock
    and the SQLCipher driver without catching either driver's error class. The
    scalar subqueries yield one row even when the record row itself is missing.
    """
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (_RECORD_TABLE,)
    ).fetchone()
    if present is None:
        return None
    floor, floor_release, last_writer = connection.execute(
        f"SELECT (SELECT floor FROM {_RECORD_TABLE} WHERE id = 1), "
        f"(SELECT floor_release FROM {_RECORD_TABLE} WHERE id = 1), "
        f"(SELECT last_writer_release FROM {_RECORD_TABLE} WHERE id = 1)"
    ).fetchone()
    return floor, floor_release, last_writer


def _floor_release(connection: Any) -> str | None:
    """Return the release recorded as having raised the floor, without writing."""
    record = _stored_record(connection)
    value = record[1] if record is not None else None
    return str(value) if value else None


def refuse_newer_store_format(connection: Any, *, path: str | Path, reader_release: str) -> int:
    """Refuse a store whose floor is above :data:`STORE_FORMAT`, changing nothing.

    Parameters
    ----------
    connection : connection
        An open connection to the event store; only reads are issued.
    path : str or pathlib.Path
        The store's path, named in the refusal.
    reader_release : str
        This release's version, named in the refusal.

    Returns
    -------
    int
        The store's floor when this release can read it.

    Raises
    ------
    StoreFormatRefused
        When the floor is above :data:`STORE_FORMAT`.
    ValueError
        When the header field is negative.
    """
    floor = stored_store_format(connection)
    if floor > STORE_FORMAT:
        raise StoreFormatRefused(
            path=str(path),
            floor=floor,
            floor_release=_floor_release(connection),
            reader_release=reader_release,
        )
    return floor


def record_store_format(connection: Any, *, release: str) -> int:
    """Record this release's format as the store's floor inside the owner's transaction.

    The caller has already passed :func:`refuse_newer_store_format` on this store,
    so its floor is at most :data:`STORE_FORMAT` and recording this release's format
    never lowers it. The record names the release that last raised the floor and the
    release that last wrote the store. When the store already holds exactly that
    record, nothing is written, so a reopen by the same release, such as a reader
    beside a running hub, adds no write to the log. Nothing is committed here; the
    store's owner commits the floor with its other opening writes.

    Parameters
    ----------
    connection : connection
        The store owner's connection, after :func:`refuse_newer_store_format` passed.
    release : str
        This release's version.

    Returns
    -------
    int
        The floor now recorded, :data:`STORE_FORMAT`.
    """
    current = stored_store_format(connection)
    floor = STORE_FORMAT
    raised_by = release if floor > current else (_floor_release(connection) or release)
    if current == floor and _stored_record(connection) == (floor, raised_by, release):
        return floor
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {_RECORD_TABLE} ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "floor INTEGER NOT NULL, "
        "floor_release TEXT NOT NULL, "
        "last_writer_release TEXT NOT NULL)"
    )
    connection.execute(
        f"INSERT INTO {_RECORD_TABLE} (id, floor, floor_release, last_writer_release) "
        "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET floor = excluded.floor, "
        "floor_release = excluded.floor_release, "
        "last_writer_release = excluded.last_writer_release",
        (floor, raised_by, release),
    )
    if current != floor:
        connection.execute(f"PRAGMA user_version = {floor}")
    return floor
