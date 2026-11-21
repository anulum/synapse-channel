# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the read-only open of the event log for offline readers
"""Read an existing event log without creating, migrating or writing it.

:class:`~synapse_channel.core.persistence.EventStore` is the log's writer: opening
it creates a missing file, adds missing tables and columns, sets the journal mode
and records the store format. An offline reader of a live journal, such as
``synapse event-query``, needs none of that, and must not take the hub's write
path. :class:`EventLogReader` opens the file through SQLite's ``mode=ro`` URI
form: a missing file is refused and nothing is created, SQLite refuses every
write, and a reader in WAL mode never blocks the hub's writer. A store whose
format is newer than this release reads is refused as it is for the writer.

The reader offers the writer's read methods only, and :class:`ReadableEventLog`
names the reads that replay, checkpoint verification and a peer fetcher need, so
those take either the writer or the reader. Rows are decoded exactly as the
writer decodes them; a malformed row comes back as the same non-secret corrupt-row
marker.

The store's own bytes are never changed. Reading a WAL journal that no process
has open leaves SQLite's empty ``-wal`` file and its ``-shm`` index beside it,
with the store's own permissions, because a read-only connection cannot
checkpoint them away; the next writer uses them and removes them on close. The
reader never deletes them itself: a hub starting at that moment would lose its
index.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

from synapse_channel.core.event_row_recovery import CorruptEventRow
from synapse_channel.core.persistence import BUSY_TIMEOUT_MS
from synapse_channel.core.persistence_api import READ_ALL_DOCUMENTATION, inherit_documentation
from synapse_channel.core.persistence_connection import StoreConnection, create_families
from synapse_channel.core.persistence_event_reads import EventReader
from synapse_channel.core.persistence_operations import OperationTable
from synapse_channel.core.persistence_sqlcipher import ReadOnlyOpenRefused, connect_event_store

if TYPE_CHECKING:
    from synapse_channel.core.persistence import StoredEvent, StoredOperation


class ReadableEventLog(Protocol):
    """The reads that journal replay, checkpoint verification and a peer fetcher make.

    Both the writer, :class:`~synapse_channel.core.persistence.EventStore`, and the
    read-only :class:`EventLogReader` satisfy it, so a caller that only reads can be
    handed either.
    """

    def iter_events(
        self,
        *,
        through_seq: int | None = None,
        kinds: Iterable[str] | None = None,
        after_seq: int | None = None,
        apply_row_quarantine: bool = True,
    ) -> Iterator[StoredEvent]:
        """Yield events in ascending sequence order without materialising the log."""

    def read_since(
        self, after_seq: int, *, kinds: Iterable[str] | None = None, limit: int | None = None
    ) -> list[StoredEvent]:
        """Return events whose sequence is greater than a cursor, in order."""

    def max_seq(self) -> int:
        """Return the highest sequence number stored, or ``0`` when the log is empty."""

    def latest_at_or_before(self, through_seq: int) -> StoredEvent | None:
        """Return the newest retained event at or below ``through_seq``."""

    def get_operation(self, operation_key: str) -> StoredOperation | None:
        """Return one completed operation, or ``None`` when it is not recorded."""


class EventLogReader:
    """An existing event log, opened for reading only.

    Parameters
    ----------
    path : str or Path
        The store file. It must exist and hold the ``events`` table.
    key_file : str or Path or None, optional
        Owner-only key file of an SQLCipher store.
    key : bytes or None, optional
        Raw key bytes of an SQLCipher store; wins over ``key_file``.

    Raises
    ------
    ReadOnlyOpenRefused
        When the file is missing or holds no event table; nothing is created.
    StoreFormatRefused
        When the store needs a newer store format than this release reads.
    SqlCipherKeyError
        When the store cannot be read with the given key, or needs one.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        key_file: str | Path | None = None,
        key: bytes | None = None,
    ) -> None:
        """Open the file read-only and check that it is an event log."""
        self.path = str(path)
        self._lock = threading.RLock()
        connection, self._encrypted = connect_event_store(
            self.path, key=key, key_file=key_file, check_same_thread=False, read_only=True
        )
        try:
            # The same wiring as the writer's; on a mode=ro connection SQLite
            # refuses any write a family might attempt.
            storage = StoreConnection(
                connection,
                self._lock,
                lambda ts, kind, payload: self._families.authentication.insert_event(
                    ts, kind, payload
                ),
                lambda row, quarantine: self._families.authentication.decode_event(
                    row, apply_row_quarantine=quarantine
                ),
                lambda transition, stamp: self._families.receipts.insert_transition(
                    transition, stamp=stamp
                ),
            )
            self._families = create_families(storage)
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'"
            ).fetchone()
            if table is None:
                raise ReadOnlyOpenRefused(
                    f"{self.path} holds no event table; a read-only open creates nothing"
                )
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    @property
    def encrypted(self) -> bool:
        """Return whether this store opened through SQLCipher page encryption."""
        return self._encrypted

    @inherit_documentation(EventReader.iter_events)
    def iter_events(
        self,
        *,
        through_seq: int | None = None,
        kinds: Iterable[str] | None = None,
        after_seq: int | None = None,
        apply_row_quarantine: bool = True,
    ) -> Iterator[StoredEvent]:
        """Yield events in ascending sequence order without materialising the log."""
        return self._families.reads.iter_events(
            through_seq=through_seq,
            kinds=kinds,
            after_seq=after_seq,
            apply_row_quarantine=apply_row_quarantine,
        )

    @contextmanager
    def stream_events(
        self, *, kinds: Iterable[str] | None = None, after_seq: int = 0
    ) -> Iterator[Iterator[StoredEvent]]:
        """Own a streaming read cursor through bounded early termination.

        Parameters
        ----------
        kinds : collections.abc.Iterable of str or None
            Optional event-kind filter, applied inside SQLite.
        after_seq : int
            Exclusive sequence floor.

        Yields
        ------
        collections.abc.Iterator of StoredEvent
            One decoded row at a time. Exiting the context closes the generator
            and releases its cursor and the reader's lock.
        """
        with closing(self._families.reads.iter_events(kinds=kinds, after_seq=after_seq)) as rows:
            yield rows

    @inherit_documentation(READ_ALL_DOCUMENTATION)
    def read_all(self) -> list[StoredEvent]:
        """Return every event in insertion order."""
        return list(self.iter_events())

    @inherit_documentation(EventReader.read_since)
    def read_since(
        self, after_seq: int, *, kinds: Iterable[str] | None = None, limit: int | None = None
    ) -> list[StoredEvent]:
        """Return events whose sequence is greater than a cursor, in order."""
        return self._families.reads.read_since(after_seq, kinds=kinds, limit=limit)

    @inherit_documentation(EventReader.read_latest)
    def read_latest(
        self, *, kinds: Iterable[str] | None = None, limit: int | None = None
    ) -> list[StoredEvent]:
        """Return the newest matching events in ascending sequence order."""
        return self._families.reads.read_latest(kinds=kinds, limit=limit)

    @inherit_documentation(EventReader.read_window)
    def read_window(
        self,
        *,
        min_seq: int | None = None,
        max_seq: int | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
        kinds: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[StoredEvent]:
        """Return events inside an inclusive sequence/time window, in order."""
        return self._families.reads.read_window(
            min_seq=min_seq,
            max_seq=max_seq,
            since_ts=since_ts,
            until_ts=until_ts,
            kinds=kinds,
            limit=limit,
        )

    @inherit_documentation(EventReader.corrupt_rows)
    def corrupt_rows(self, *, through_seq: int | None = None) -> tuple[CorruptEventRow, ...]:
        """Return safe forensic markers for every malformed row in sequence order."""
        return self._families.reads.corrupt_rows(through_seq=through_seq)

    @inherit_documentation(EventReader.count)
    def count(self) -> int:
        """Return the number of events currently stored."""
        return self._families.reads.count()

    @inherit_documentation(EventReader.event_metrics)
    def event_metrics(self, window_seconds: Mapping[str, float]) -> dict[str, object]:
        """Return retained metadata aggregates through the store's keyed reader."""
        return self._families.reads.event_metrics(window_seconds)

    @inherit_documentation(EventReader.max_seq)
    def max_seq(self) -> int:
        """Return the highest sequence number stored, or ``0`` when the log is empty."""
        return self._families.reads.max_seq()

    @inherit_documentation(EventReader.latest_at_or_before)
    def latest_at_or_before(self, through_seq: int) -> StoredEvent | None:
        """Return the newest retained event at or below ``through_seq``."""
        return self._families.reads.latest_at_or_before(through_seq)

    @inherit_documentation(OperationTable.get_operation)
    def get_operation(self, operation_key: str) -> StoredOperation | None:
        """Return one completed operation without exposing the key in errors."""
        return self._families.operations.get_operation(operation_key)

    def close(self) -> None:
        """Close the read-only connection."""
        with self._lock:
            self._connection.close()

    def __enter__(self) -> EventLogReader:
        """Enter a context manager that closes the reader on exit."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the reader when leaving the context."""
        self.close()
