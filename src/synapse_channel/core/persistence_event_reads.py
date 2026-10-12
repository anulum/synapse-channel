# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""EventReader implementation for the existing EventStore facade."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from synapse_channel.core.event_row_recovery import (
    CorruptEventRow,
    decode_event_row,
)
from synapse_channel.core.persistence_connection import StoreConnection

if TYPE_CHECKING:
    from synapse_channel.core.persistence import (
        StoredEvent,
    )
logger = logging.getLogger("synapse_channel.core.persistence")


class EventReader:
    """Own this table family under the shared connection and lock."""

    def __init__(self, storage: StoreConnection) -> None:
        """Bind the existing owner connection without opening a second one."""
        self.storage = storage

    def iter_events(
        self,
        *,
        through_seq: int | None = None,
        kinds: Iterable[str] | None = None,
        after_seq: int | None = None,
        apply_row_quarantine: bool = True,
    ) -> Iterator[StoredEvent]:
        """Yield events in ascending sequence order without materialising the log.

        This is the bounded-memory read seam for whole-log folds (the Merkle
        commitment, causality reconstruction): rows stream off the SQLite cursor
        one at a time, so the peak footprint is one event, not the log. A kind
        filter is applied inside SQLite, so uninterested kinds (bulk chat on a
        long-lived hub) never cross into Python at all.

        Parameters
        ----------
        through_seq : int or None, optional
            Inclusive sequence ceiling; events after it are not yielded. ``None``
            streams the whole log.
        kinds : Iterable[str] or None, optional
            When given, restrict the stream to these event kinds; an empty
            iterable yields nothing. ``None`` streams every kind.
        after_seq : int or None, optional
            Exclusive sequence floor; only events after it are yielded, so an
            incremental fold reads just what it has not seen. ``None`` starts at
            the beginning of the log.
        apply_row_quarantine : bool, optional
            Return rows quarantined by :meth:`enable_row_mac` as markers (the
            default). The Merkle commitment passes ``False`` to fold the raw rows.

        Yields
        ------
        StoredEvent
            Each matching event at or below the ceiling, by ascending sequence.
        """
        sql = "SELECT seq, ts, kind, payload FROM events"
        clauses: list[str] = []
        params: list[Any] = []
        if through_seq is not None:
            clauses.append("seq <= ?")
            params.append(int(through_seq))
        if after_seq is not None:
            clauses.append("seq > ?")
            params.append(int(after_seq))
        if kinds is not None:
            kind_list = [str(k) for k in kinds]
            if not kind_list:
                return
            clauses.append(f"kind IN ({','.join('?' for _ in kind_list)})")
            params.extend(kind_list)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq"
        with self.storage.lock:
            for row in self.storage.connection.execute(sql, params):
                yield self.storage.decode_event(row, apply_row_quarantine)

    def read_since(
        self, after_seq: int, *, kinds: Iterable[str] | None = None, limit: int | None = None
    ) -> list[StoredEvent]:
        """Return events whose sequence is greater than a cursor, in order.

        This is the durable, presence-free ingest seam a downstream
        persistent-memory adapter polls: it tracks the last sequence it consumed,
        calls :meth:`read_since` with it, processes the batch, and advances —
        resuming with no loss or duplication across hub restarts, because the
        sequence is a monotonic primary key.

        Parameters
        ----------
        after_seq : int
            Exclusive lower bound; only events with ``seq > after_seq`` are
            returned. Pass ``0`` for the whole log.
        kinds : Iterable[str] or None, optional
            When given, restrict the result to these event kinds (e.g.
            :data:`~synapse_channel.core.journal.MEMORY_KINDS`); an empty iterable
            returns nothing. ``None`` returns every kind.
        limit : int or None, optional
            Cap the batch size (floored at ``0``); ``None`` returns all matching
            events. The cap applies after ordering, so repeated calls walk the log
            forward in fixed-size batches.

        Returns
        -------
        list[StoredEvent]
            Matching events ordered by ascending sequence number.
        """
        sql = "SELECT seq, ts, kind, payload FROM events WHERE seq > ?"
        params: list[Any] = [int(after_seq)]
        if kinds is not None:
            kind_list = [str(k) for k in kinds]
            if not kind_list:
                return []
            sql += f" AND kind IN ({','.join('?' for _ in kind_list)})"
            params.extend(kind_list)
        sql += " ORDER BY seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self.storage.lock:
            rows = self.storage.connection.execute(sql, params).fetchall()
        return [self.storage.decode_event(row, True) for row in rows]

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
        """Return events inside an inclusive sequence/time window, in order.

        This is the selective-read seam the event-query layer uses to avoid
        loading an unbounded event store for every point-in-time or windowed
        query: the bounds are pushed into SQLite so only candidate rows are
        deserialised. Every bound is optional and inclusive; omitting all of them
        is equivalent to :meth:`read_all`.

        Parameters
        ----------
        min_seq, max_seq : int or None, optional
            Inclusive lower and upper sequence bounds (``seq >= min_seq`` /
            ``seq <= max_seq``).
        since_ts, until_ts : float or None, optional
            Inclusive lower and upper timestamp bounds (``ts >= since_ts`` /
            ``ts <= until_ts``).
        kinds : Iterable[str] or None, optional
            Restrict to these event kinds; an empty iterable returns nothing.
        limit : int or None, optional
            Cap the number of rows returned after ordering (floored at ``0``).

        Returns
        -------
        list[StoredEvent]
            Matching events ordered by ascending sequence number.
        """
        sql = "SELECT seq, ts, kind, payload FROM events WHERE 1 = 1"
        params: list[Any] = []
        if min_seq is not None:
            sql += " AND seq >= ?"
            params.append(int(min_seq))
        if max_seq is not None:
            sql += " AND seq <= ?"
            params.append(int(max_seq))
        if since_ts is not None:
            sql += " AND ts >= ?"
            params.append(float(since_ts))
        if until_ts is not None:
            sql += " AND ts <= ?"
            params.append(float(until_ts))
        if kinds is not None:
            kind_list = [str(k) for k in kinds]
            if not kind_list:
                return []
            sql += f" AND kind IN ({','.join('?' for _ in kind_list)})"
            params.extend(kind_list)
        sql += " ORDER BY seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self.storage.lock:
            rows = self.storage.connection.execute(sql, params).fetchall()
        return [self.storage.decode_event(row, True) for row in rows]

    def corrupt_rows(self, *, through_seq: int | None = None) -> tuple[CorruptEventRow, ...]:
        """Return safe forensic markers for every malformed row in sequence order.

        Parameters
        ----------
        through_seq : int or None, optional
            Inclusive sequence ceiling. ``None`` scans the complete event log.

        Returns
        -------
        tuple[CorruptEventRow, ...]
            Markers contain reasons and a raw-payload digest, never raw payload
            bytes. This scan is the operator seam used by explicit compaction.
        """
        sql = "SELECT seq, ts, kind, payload FROM events"
        params: tuple[int, ...] = ()
        if through_seq is not None:
            sql += " WHERE seq <= ?"
            params = (int(through_seq),)
        sql += " ORDER BY seq"
        corrupt: list[CorruptEventRow] = []
        with self.storage.lock:
            for row in self.storage.connection.execute(sql, params):
                marker = decode_event_row(row).corruption
                if marker is not None:
                    corrupt.append(marker)
        return tuple(corrupt)

    def count(self) -> int:
        """Return the number of events currently stored."""
        with self.storage.lock:
            row = self.storage.connection.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])

    def max_seq(self) -> int:
        """Return the highest sequence number stored, or ``0`` when the log is empty.

        Useful as a fully-settled compaction floor: with no read-side consumer
        lagging behind, the whole log up to the latest sequence may be compacted
        (see :mod:`synapse_channel.core.compaction`).
        """
        with self.storage.lock:
            row = self.storage.connection.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events"
            ).fetchone()
        return int(row[0])

    def latest_at_or_before(self, through_seq: int) -> StoredEvent | None:
        """Return the newest retained event at or below ``through_seq``.

        A direct descending primary-key lookup keeps state-at projections from
        decoding the whole journal merely to obtain their deterministic clock.
        Sequence gaps left by retention are handled by selecting the preceding
        retained event; an empty prefix returns ``None``.
        """
        with self.storage.lock:
            row = self.storage.connection.execute(
                (
                    "SELECT seq, ts, kind, payload FROM events WHERE seq <= ? ORDER BY seq "
                    "DESC LIMIT 1"
                ),
                (int(through_seq),),
            ).fetchone()
        return None if row is None else self.storage.decode_event(row, True)
