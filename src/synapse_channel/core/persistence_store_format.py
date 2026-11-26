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

The same record names the store's journal instance: a random identifier written
once, when the store is created or when a writer first opens an older store, with
the sequence floor it starts from. A forward feed binds its cursors to it, so a
consumer can tell this journal from another one under the same hub id. An
ordinary open never rewrites it, and a reader never writes it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STORE_FORMAT = 1
"""The store format this release writes, and the highest one it reads.

Format 1 is the event-store layout of the first release that records a floor.
"""

# The statements below name this table literally: SQL built from it would be flagged as
# string-built (bandit B608), and the name is fixed.
_RECORD_TABLE = "store_format"


@dataclass(frozen=True)
class JournalInstance:
    """The store's journal instance and the sequence floor it starts from.

    Attributes
    ----------
    instance : str
        32 lower-case hexadecimal digits from :func:`secrets.token_hex`.
    floor : int
        The highest sequence the store held when the instance was written; rows at
        or below it were written before the instance existed.
    """

    instance: str
    floor: int


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
        "SELECT (SELECT floor FROM store_format WHERE id = 1), "
        "(SELECT floor_release FROM store_format WHERE id = 1), "
        "(SELECT last_writer_release FROM store_format WHERE id = 1)"
    ).fetchone()
    return floor, floor_release, last_writer


def _record_columns(connection: Any) -> set[str]:
    """Return the record table's column names, or an empty set without the table."""
    return {str(row[1]) for row in connection.execute("PRAGMA table_info(store_format)")}


def read_journal_instance(connection: Any) -> JournalInstance | None:
    """Return the store's journal instance, reading only.

    Parameters
    ----------
    connection : connection
        An open SQLite or SQLCipher DB-API connection to the event store.

    Returns
    -------
    JournalInstance or None
        The recorded instance, or ``None`` for a store that has none yet: no record
        table, a record from before the instance columns, or no value written.
    """
    if not {"journal_instance", "instance_floor"} <= _record_columns(connection):
        return None
    row = connection.execute(
        "SELECT journal_instance, instance_floor FROM store_format WHERE id = 1"
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return JournalInstance(instance=str(row[0]), floor=int(row[1]))


def _assign_journal_instance(connection: Any) -> None:
    """Write a new journal instance with the current sequence floor, once.

    The columns are added to a record that predates them; older readers ignore
    them, so the format does not change. The update only fills an empty value, so
    it can never replace an instance.
    """
    columns = _record_columns(connection)
    if "journal_instance" not in columns:
        connection.execute("ALTER TABLE store_format ADD COLUMN journal_instance TEXT")
    if "instance_floor" not in columns:
        connection.execute("ALTER TABLE store_format ADD COLUMN instance_floor INTEGER")
    floor = int(connection.execute("SELECT coalesce(max(seq), 0) FROM events").fetchone()[0])
    connection.execute(
        "UPDATE store_format SET journal_instance = ?, instance_floor = ? "
        "WHERE id = 1 AND journal_instance IS NULL",
        (secrets.token_hex(16), floor),
    )


def forget_copied_journal_instance(connection: Any) -> None:
    """Clear the journal instance that a whole-database copy carried over from its source.

    A copy is a different journal from its source: both files could go on accepting
    rows, so they must never share an instance. After the value is cleared,
    :func:`record_store_format` draws a new instance, with the copy's highest sequence
    as its floor, exactly as it does for a copy that started without a record. Only a
    copy that no hub has served yet is ever passed here; a live store keeps its instance.
    A record from before the instance columns has nothing to clear. Nothing is committed.

    Parameters
    ----------
    connection : connection
        The new copy's connection, before :func:`record_store_format`.
    """
    if {"journal_instance", "instance_floor"} <= _record_columns(connection):
        connection.execute(
            "UPDATE store_format SET journal_instance = NULL, instance_floor = NULL WHERE id = 1"
        )


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


def receipt_backfill_watermark(connection: Any) -> int | None:
    """Return the highest event sequence covered by receipt backfill, reading only.

    Parameters
    ----------
    connection : connection
        An open SQLite or SQLCipher DB-API connection to the event store.

    Returns
    -------
    int or None
        The durably recorded ``receipt_backfill_through_seq`` watermark, or
        ``None`` for a new or pre-watermark store.
    """
    if "receipt_backfill_through_seq" not in _record_columns(connection):
        return None
    row = connection.execute(
        "SELECT receipt_backfill_through_seq FROM store_format WHERE id = 1"
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def record_receipt_backfill_watermark(connection: Any, *, through_seq: int) -> int:
    """Durably record or advance the receipt backfill watermark inside the owner's transaction.

    The caller has already run :func:`record_store_format`, so ``store_format``
    row ``id = 1`` exists. When the recorded watermark already covers
    ``through_seq``, nothing is written so a reopen issues no write
    transaction. Nothing is committed here; the store's owner commits the
    watermark atomically with any backfilled rows.

    Parameters
    ----------
    connection : connection
        The store owner's connection, after :func:`record_store_format`.
    through_seq : int
        Highest receipt event sequence covered by backfill or transactional writes.

    Returns
    -------
    int
        The durable watermark now in force.
    """
    target = max(0, int(through_seq))
    current = receipt_backfill_watermark(connection)
    if current is not None and current >= target:
        return current
    if "receipt_backfill_through_seq" not in _record_columns(connection):
        connection.execute(
            "ALTER TABLE store_format ADD COLUMN receipt_backfill_through_seq INTEGER"
        )
    connection.execute(
        "UPDATE store_format SET receipt_backfill_through_seq = ? WHERE id = 1",
        (target,),
    )
    return target


def record_store_format(connection: Any, *, release: str) -> int:
    """Record this release's format as the store's floor inside the owner's transaction.

    The caller has already passed :func:`refuse_newer_store_format` on this store,
    so its floor is at most :data:`STORE_FORMAT` and recording this release's format
    never lowers it. The record names the release that last raised the floor and the
    release that last wrote the store, and the store's journal instance, written
    once when it is missing (:func:`read_journal_instance`). When the store already
    holds exactly that record and an instance, nothing is written, so a reopen by
    the same release, such as a reader beside a running hub, adds no write to the
    log. Nothing is committed here; the store's owner commits the floor with its
    other opening writes.

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
    record_current = current == floor and _stored_record(connection) == (
        floor,
        raised_by,
        release,
    )
    if not record_current:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS store_format ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "floor INTEGER NOT NULL, "
            "floor_release TEXT NOT NULL, "
            "last_writer_release TEXT NOT NULL, "
            "journal_instance TEXT, "
            "instance_floor INTEGER, "
            "receipt_backfill_through_seq INTEGER)"
        )
        # The update names the release columns only: an instance is never rewritten.
        connection.execute(
            "INSERT INTO store_format (id, floor, floor_release, last_writer_release) "
            "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET floor = excluded.floor, "
            "floor_release = excluded.floor_release, "
            "last_writer_release = excluded.last_writer_release",
            (floor, raised_by, release),
        )
        if current != floor:
            connection.execute(f"PRAGMA user_version = {floor}")
    if read_journal_instance(connection) is None:
        _assign_journal_instance(connection)
    return floor
